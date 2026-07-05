#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cpa_sub2api_bridge.py

Universal bridge converter for CPA <-> sub2api.

Sub2api output schema: v2 account export format provided by user on 2026-07-05.

Auto mode:
- CPA-style input package/folder/JSON -> sub2api JSON
- sub2api JSON/link -> CPA ZIP

Input supported:
- Folder containing JSON files
- Single JSON file
- HTTP/HTTPS JSON/archive link
- ZIP, TAR, TAR.GZ, TGZ, TAR.BZ2, TBZ2, TAR.XZ, TXZ
- Single GZ/BZ2/XZ compressed JSON files
- Nested archives up to a safe recursion depth
- RAR/7Z/CAB/ZIPX and many other archive formats when 7-Zip or WinRAR/UnRAR is installed

No tokens are printed to the console.
"""

from __future__ import annotations

import argparse
import base64
import bz2
import gzip
import io
import json
import lzma
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

DEFAULT_ACCOUNT_TEMPLATE: Dict[str, Any] = {
    "platform": "openai",
    "type": "oauth",
    "concurrency": 10,
    "priority": 1,
    "rate_multiplier": 1,
    "auto_pause_on_expired": True,
}

# Canonical sub2api schema field order. This intentionally matches the newer
# sample export format: account fields are name/platform/type/credentials/extra,
# then runtime controls. Tokens are never printed to console.
DEFAULT_MODEL_MAPPING: Dict[str, str] = {
    "gpt-5.5": "gpt-5.5",
    "gpt-image-2": "gpt-image-2",
}

STANDARD_CREDENTIAL_KEYS = [
    "_token_version",
    "access_token",
    "chatgpt_account_id",
    "chatgpt_user_id",
    "client_id",
    "email",
    "expires_at",
    "id_token",
    "model_mapping",
    "organization_id",
    "plan_type",
    "refresh_token",
    "subscription_expires_at",
]

STANDARD_EXTRA_KEYS = [
    "codex_5h_reset_after_seconds",
    "codex_5h_reset_at",
    "codex_5h_used_percent",
    "codex_5h_window_minutes",
    "codex_7d_reset_after_seconds",
    "codex_7d_reset_at",
    "codex_7d_used_percent",
    "codex_7d_window_minutes",
    "codex_primary_over_secondary_percent",
    "codex_primary_reset_after_seconds",
    "codex_primary_used_percent",
    "codex_primary_window_minutes",
    "codex_secondary_reset_after_seconds",
    "codex_secondary_used_percent",
    "codex_secondary_window_minutes",
    "codex_usage_updated_at",
    "email",
    "openai_oauth_responses_websockets_v2_enabled",
    "openai_oauth_responses_websockets_v2_mode",
    "openai_passthrough",
    "privacy_mode",
]

DEFAULT_EXTRA_VALUES: Dict[str, Any] = {
    "codex_5h_reset_after_seconds": 0,
    "codex_5h_reset_at": "",
    "codex_5h_used_percent": 0,
    "codex_5h_window_minutes": 300,
    "codex_7d_reset_after_seconds": 0,
    "codex_7d_reset_at": "",
    "codex_7d_used_percent": 0,
    "codex_7d_window_minutes": 10080,
    "codex_primary_over_secondary_percent": 0,
    "codex_primary_reset_after_seconds": 0,
    "codex_primary_used_percent": 0,
    "codex_primary_window_minutes": 300,
    "codex_secondary_reset_after_seconds": 0,
    "codex_secondary_used_percent": 0,
    "codex_secondary_window_minutes": 10080,
    "codex_usage_updated_at": "",
    "openai_oauth_responses_websockets_v2_enabled": True,
    "openai_oauth_responses_websockets_v2_mode": "ctx_pool",
    "openai_passthrough": True,
    "privacy_mode": "training_off",
}

TOKEN_KEYS = ("access_token", "token", "accessToken")
MAX_NESTED_DEPTH = 5

EXTERNAL_ARCHIVE_EXTS = {
    ".7z", ".rar", ".zipx", ".cab", ".iso", ".arj", ".lzh", ".lha", ".z", ".wim",
    ".chm", ".cpio", ".deb", ".rpm", ".xar", ".dmg", ".001",
}

ALL_ARCHIVE_EXTS = EXTERNAL_ARCHIVE_EXTS | {
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".tbz", ".tbz2", ".xz", ".txz",
    ".tar.gz", ".tar.bz2", ".tar.xz",
}


def now_iso_z(ms: bool = True) -> str:
    timespec = "milliseconds" if ms else "seconds"
    return datetime.now(timezone.utc).isoformat(timespec=timespec).replace("+00:00", "Z")


def current_epoch_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def epoch_to_iso_z(value: Any, ms: bool = True) -> Optional[str]:
    try:
        if value is None or value == "":
            return None
        sec = int(float(value))
        timespec = "milliseconds" if ms else "seconds"
        return datetime.fromtimestamp(sec, timezone.utc).isoformat(timespec=timespec).replace("+00:00", "Z")
    except Exception:
        return None


def iso_to_epoch(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return int(float(text))
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return int(datetime.fromisoformat(text).timestamp())
    except Exception:
        return None


def normalize_iso_seconds(value: Any) -> Optional[str]:
    epoch = iso_to_epoch(value)
    if epoch is not None:
        return epoch_to_iso_z(epoch, ms=False)
    return None


def b64url_decode_json(part: str) -> Dict[str, Any]:
    try:
        padded = part + "=" * ((4 - len(part) % 4) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(raw.decode("utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def decode_jwt_payload(token: str) -> Dict[str, Any]:
    if not isinstance(token, str):
        return {}
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    return b64url_decode_json(parts[1])


def normalize_email_key(email: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", email.lower()).strip("_")


def safe_filename(name: str, fallback: str = "account") -> str:
    name = (name or fallback).strip()
    name = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", name)
    name = name.strip(" ._")
    return name or fallback


def first_nonempty(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def find_access_token(obj: Dict[str, Any]) -> Optional[str]:
    cred = obj.get("credentials") if isinstance(obj.get("credentials"), dict) else {}
    for source in (cred, obj):
        for key in TOKEN_KEYS:
            val = source.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


def load_json_from_bytes(raw: bytes, label: str) -> Any:
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return json.loads(raw.decode(enc))
        except Exception:
            pass
    raise ValueError(f"Could not parse JSON in {label}")


def strip_archive_suffix_name(name: str) -> str:
    for suffix in sorted(ALL_ARCHIVE_EXTS | {".json"}, key=len, reverse=True):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem or "output"


def strip_archive_suffix(path: Path) -> str:
    return strip_archive_suffix_name(path.name)


def default_output_path(input_path: Path, direction: str, source_is_url: bool = False) -> Path:
    if source_is_url:
        base = strip_archive_suffix_name(input_path.name or "download") or "download"
        cwd = Path.cwd()
        return cwd / (base + (".cpa.zip" if direction == "cpa" else ".sub2api.json"))
    if input_path.is_file():
        base = strip_archive_suffix(input_path)
        if not base:
            base = input_path.stem or "output"
        return input_path.with_name(base + (".cpa.zip" if direction == "cpa" else ".sub2api.json"))
    return input_path.with_name(input_path.name.rstrip("\\/") + (".cpa.zip" if direction == "cpa" else ".sub2api.json"))


def looks_like_json(raw: bytes) -> bool:
    sample = raw[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    return sample.startswith(b"{") or sample.startswith(b"[")


def looks_like_html(raw: bytes) -> bool:
    sample = raw[:1024].lstrip().lower()
    return sample.startswith(b"<!doctype html") or sample.startswith(b"<html") or sample.startswith(b"<head")


def detect_kind_from_bytes(raw: bytes, label: str = "") -> str:
    head = raw[:16]
    lower = label.lower()
    if looks_like_json(raw):
        return "json"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06") or head.startswith(b"PK\x07\x08"):
        return "zip"
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    if head.startswith(b"BZh"):
        return "bzip2"
    if head.startswith(b"\xfd7zXZ\x00"):
        return "xz"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    if head.startswith(b"Rar!\x1a\x07"):
        return "rar"
    if lower.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")):
        return "tar"
    if looks_like_html(raw):
        return "html"
    return "unknown"


def detect_kind_from_path(path: Path) -> str:
    if path.is_dir():
        return "folder"
    try:
        with path.open("rb") as f:
            head = f.read(4096)
    except Exception:
        return "unknown"

    kind = detect_kind_from_bytes(head, path.name)
    if kind in {"json", "zip", "gzip", "bzip2", "xz", "7z", "rar", "html"}:
        return kind
    try:
        if tarfile.is_tarfile(path):
            return "tar"
    except Exception:
        pass
    try:
        if zipfile.is_zipfile(path):
            return "zip"
    except Exception:
        pass
    if path.suffix.lower() in EXTERNAL_ARCHIVE_EXTS:
        return "external"
    return kind


def find_external_extractor() -> Optional[Tuple[str, List[str]]]:
    candidates: List[Tuple[str, List[str]]] = []
    for exe in ("7z", "7za", "7zr"):
        found = shutil.which(exe)
        if found:
            candidates.append(("7z", [found]))

    env_paths = []
    for key in ("ProgramFiles", "ProgramFiles(x86)"):
        val = os.environ.get(key)
        if val:
            env_paths.append(Path(val))
    possible_7z = [p / "7-Zip" / "7z.exe" for p in env_paths]
    possible_rar = [p / "WinRAR" / "UnRAR.exe" for p in env_paths] + [p / "WinRAR" / "Rar.exe" for p in env_paths]
    for p in possible_7z:
        if p.exists():
            candidates.append(("7z", [str(p)]))
    for p in possible_rar:
        if p.exists():
            candidates.append(("unrar", [str(p)]))
    return candidates[0] if candidates else None


def extract_with_external_tool(archive_path: Path, dest_dir: Path) -> None:
    tool = find_external_extractor()
    if not tool:
        raise RuntimeError(
            "This archive needs an external extractor. Install 7-Zip, then run again. "
            "7-Zip supports RAR/7Z/CAB/ZIPX and many other formats."
        )
    kind, cmd_prefix = tool
    if kind == "7z":
        cmd = cmd_prefix + ["x", "-y", f"-o{str(dest_dir)}", str(archive_path)]
    else:
        cmd = cmd_prefix + ["x", "-y", str(archive_path), str(dest_dir) + os.sep]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    if proc.returncode != 0:
        tail = (proc.stdout or "").strip().splitlines()[-8:]
        raise RuntimeError("External extractor failed:\n" + "\n".join(tail))


def iter_jsons_from_zip_path(path: Path, label_prefix: str, depth: int) -> Iterator[Tuple[str, Any]]:
    with zipfile.ZipFile(path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            inner_label = f"{label_prefix}/{info.filename}"
            try:
                raw = zf.read(info)
                yield from iter_jsons_from_bytes(raw, inner_label, depth + 1)
            except Exception as exc:
                if info.filename.lower().endswith(".json"):
                    yield inner_label, {"__parse_error__": str(exc)}


def iter_jsons_from_tar_path(path: Path, label_prefix: str, depth: int) -> Iterator[Tuple[str, Any]]:
    with tarfile.open(path, "r:*") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            inner_label = f"{label_prefix}/{member.name}"
            try:
                f = tf.extractfile(member)
                if not f:
                    continue
                raw = f.read()
                yield from iter_jsons_from_bytes(raw, inner_label, depth + 1)
            except Exception as exc:
                if member.name.lower().endswith(".json"):
                    yield inner_label, {"__parse_error__": str(exc)}


def try_iter_tar_from_bytes(raw: bytes, label: str, depth: int) -> Optional[List[Tuple[str, Any]]]:
    try:
        out: List[Tuple[str, Any]] = []
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                inner_label = f"{label}/{member.name}"
                f = tf.extractfile(member)
                if not f:
                    continue
                out.extend(iter_jsons_from_bytes(f.read(), inner_label, depth + 1))
        return out
    except Exception:
        return None


def iter_jsons_from_bytes(raw: bytes, label: str, depth: int) -> Iterator[Tuple[str, Any]]:
    if depth > MAX_NESTED_DEPTH:
        if label.lower().endswith(".json"):
            yield label, {"__parse_error__": "Nested archive depth limit reached"}
        return

    kind = detect_kind_from_bytes(raw, label)

    if kind == "json" or label.lower().endswith(".json"):
        try:
            yield label, load_json_from_bytes(raw, label)
        except Exception as exc:
            yield label, {"__parse_error__": str(exc)}
        return

    if kind == "zip":
        try:
            with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    inner_label = f"{label}/{info.filename}"
                    try:
                        yield from iter_jsons_from_bytes(zf.read(info), inner_label, depth + 1)
                    except Exception as exc:
                        if info.filename.lower().endswith(".json"):
                            yield inner_label, {"__parse_error__": str(exc)}
        except Exception as exc:
            yield label, {"__parse_error__": f"ZIP read failed: {exc}"}
        return

    if kind in {"tar", "gzip", "bzip2", "xz"}:
        tar_result = try_iter_tar_from_bytes(raw, label, depth)
        if tar_result is not None:
            for item in tar_result:
                yield item
            return

    if kind == "gzip":
        try:
            yield from iter_jsons_from_bytes(gzip.decompress(raw), label + ".ungz", depth + 1)
        except Exception as exc:
            yield label, {"__parse_error__": f"GZ decompress failed: {exc}"}
        return

    if kind == "bzip2":
        try:
            yield from iter_jsons_from_bytes(bz2.decompress(raw), label + ".unbz2", depth + 1)
        except Exception as exc:
            yield label, {"__parse_error__": f"BZ2 decompress failed: {exc}"}
        return

    if kind == "xz":
        try:
            yield from iter_jsons_from_bytes(lzma.decompress(raw), label + ".unxz", depth + 1)
        except Exception as exc:
            yield label, {"__parse_error__": f"XZ decompress failed: {exc}"}
        return

    if kind in {"7z", "rar"} or Path(label).suffix.lower() in EXTERNAL_ARCHIVE_EXTS:
        with tempfile.TemporaryDirectory(prefix="bridge_nested_") as td:
            temp_archive = Path(td) / Path(label).name
            temp_archive.write_bytes(raw)
            out_dir = Path(td) / "out"
            out_dir.mkdir()
            try:
                extract_with_external_tool(temp_archive, out_dir)
                yield from iter_jsons_from_path(out_dir, label + ".extracted", depth + 1)
            except Exception as exc:
                yield label, {"__parse_error__": str(exc)}
        return

    return


def iter_jsons_from_path(input_path: Path, label_prefix: Optional[str] = None, depth: int = 0) -> Iterator[Tuple[str, Any]]:
    input_path = input_path.expanduser()
    label_prefix = label_prefix or str(input_path)

    if not input_path.exists():
        raise FileNotFoundError(f"Input not found: {input_path}")

    if input_path.is_dir():
        for file in sorted(p for p in input_path.rglob("*") if p.is_file()):
            rel = str(file.relative_to(input_path))
            try:
                yield from iter_jsons_from_path(file, f"{label_prefix}/{rel}", depth + 1)
            except Exception as exc:
                if file.suffix.lower() == ".json":
                    yield f"{label_prefix}/{rel}", {"__parse_error__": str(exc)}
        return

    kind = detect_kind_from_path(input_path)

    if kind == "html":
        raise RuntimeError(
            "Input looks like an HTML/web page, not an archive/JSON. "
            "The download may have failed or saved an error page."
        )

    if kind == "json":
        yield str(input_path), load_json_from_bytes(input_path.read_bytes(), str(input_path))
        return

    if kind == "zip":
        try:
            yield from iter_jsons_from_zip_path(input_path, label_prefix, depth)
            return
        except zipfile.BadZipFile:
            pass

    if kind == "tar":
        yield from iter_jsons_from_tar_path(input_path, label_prefix, depth)
        return

    if kind in {"gzip", "bzip2", "xz"}:
        raw = input_path.read_bytes()
        yield from iter_jsons_from_bytes(raw, label_prefix, depth)
        return

    if kind in {"7z", "rar", "external"} or input_path.suffix.lower() in EXTERNAL_ARCHIVE_EXTS:
        with tempfile.TemporaryDirectory(prefix="bridge_extract_") as td:
            out_dir = Path(td) / "out"
            out_dir.mkdir()
            extract_with_external_tool(input_path, out_dir)
            yield from iter_jsons_from_path(out_dir, label_prefix + ".extracted", depth + 1)
        return

    if input_path.suffix.lower() == ".json":
        yield str(input_path), load_json_from_bytes(input_path.read_bytes(), str(input_path))
        return

    raise RuntimeError(
        f"Unsupported or invalid input file: {input_path}\n"
        "Supported without extra tools: folder, JSON, ZIP, TAR, TAR.GZ/TGZ, TAR.BZ2, TAR.XZ, GZ/BZ2/XZ JSON.\n"
        "For RAR/7Z/CAB/ZIPX and many other formats, install 7-Zip and run again."
    )


def is_url(text: str) -> bool:
    try:
        p = urllib.parse.urlparse(text)
        return p.scheme.lower() in {"http", "https"} and bool(p.netloc)
    except Exception:
        return False


def filename_from_response(url: str, headers: Message) -> str:
    cd = headers.get("Content-Disposition", "") if headers else ""
    # Simple Content-Disposition filename support.
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd, flags=re.I)
    if m:
        name = urllib.parse.unquote(m.group(1)).strip()
        if name:
            return safe_filename(name, "download")
    path_name = Path(urllib.parse.urlparse(url).path).name
    return safe_filename(urllib.parse.unquote(path_name), "download.json")


@contextmanager
def resolve_input_to_path(source: str) -> Iterator[Tuple[Path, bool]]:
    if is_url(source):
        with tempfile.TemporaryDirectory(prefix="bridge_download_") as td:
            req = urllib.request.Request(
                source,
                headers={
                    "User-Agent": "Mozilla/5.0 cpa-sub2api-bridge/1.0",
                    "Accept": "application/json, application/zip, application/octet-stream, */*",
                },
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                if not raw:
                    raise RuntimeError("Downloaded link is empty.")
                name = filename_from_response(source, resp.headers)
                kind = detect_kind_from_bytes(raw, name)
                if kind == "json" and not name.lower().endswith(".json"):
                    name += ".json"
                elif kind == "zip" and not name.lower().endswith((".zip", ".zipx")):
                    name += ".zip"
                local = Path(td) / name
                local.write_bytes(raw)
                yield local, True
        return
    yield Path(source).expanduser(), False


def iter_candidate_objects(data: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(data, dict):
        if data.get("__parse_error__"):
            return
        if isinstance(data.get("accounts"), list):
            for item in data["accounts"]:
                if isinstance(item, dict):
                    yield item
            return
        if find_access_token(data):
            yield data
            return
        for val in data.values():
            if isinstance(val, dict):
                if find_access_token(val):
                    yield val
                else:
                    yield from iter_candidate_objects(val)
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        yield from iter_candidate_objects(item)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                yield from iter_candidate_objects(item)


def is_sub2api_export(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    accounts = data.get("accounts")
    if not isinstance(accounts, list) or not accounts:
        return False
    has_sub2api_mark = "exported_at" in data or "proxies" in data
    if not has_sub2api_mark:
        return False
    for item in accounts[:5]:
        if isinstance(item, dict) and isinstance(item.get("credentials"), dict) and find_access_token(item):
            return True
    return False


def detect_direction(input_path: Path) -> Tuple[str, int, int]:
    json_count = 0
    sub2api_count = 0
    for _label, data in iter_jsons_from_path(input_path):
        json_count += 1
        if is_sub2api_export(data):
            sub2api_count += 1
    return ("cpa" if sub2api_count > 0 else "sub2api", json_count, sub2api_count)


def load_reference(reference_path: Optional[Path]) -> Tuple[List[Any], Dict[str, Any]]:
    """Load optional sub2api reference only for non-secret defaults/order.

    The output schema is always the v2 format. Reference files can override
    proxies and harmless runtime defaults, but credentials from the reference
    are never copied to converted accounts.
    """
    template = DEFAULT_ACCOUNT_TEMPLATE.copy()
    template["_model_mapping"] = DEFAULT_MODEL_MAPPING.copy()
    template["_extra_defaults"] = DEFAULT_EXTRA_VALUES.copy()
    if not reference_path:
        return [], template
    if not reference_path.exists():
        raise FileNotFoundError(f"Reference file not found: {reference_path}")
    ref = load_json_from_bytes(reference_path.read_bytes(), str(reference_path))
    if not isinstance(ref, dict):
        return [], template

    proxies = ref.get("proxies") if isinstance(ref.get("proxies"), list) else []
    accounts = ref.get("accounts") if isinstance(ref.get("accounts"), list) else []
    if accounts and isinstance(accounts[0], dict):
        first = accounts[0]
        for key in ("platform", "type", "concurrency", "priority", "rate_multiplier", "auto_pause_on_expired"):
            if key in first:
                template[key] = first[key]
        cred = first.get("credentials") if isinstance(first.get("credentials"), dict) else {}
        if isinstance(cred.get("model_mapping"), dict):
            template["_model_mapping"] = cred["model_mapping"].copy()
        extra = first.get("extra") if isinstance(first.get("extra"), dict) else {}
        for key in DEFAULT_EXTRA_VALUES:
            if key in extra:
                template["_extra_defaults"][key] = extra[key]
    return proxies, template


def get_first_org_id(id_auth: Dict[str, Any]) -> Optional[str]:
    orgs = id_auth.get("organizations")
    if isinstance(orgs, list):
        default_org = None
        first_org = None
        for org in orgs:
            if not isinstance(org, dict):
                continue
            if not first_org and org.get("id"):
                first_org = str(org["id"])
            if org.get("is_default") and org.get("id"):
                default_org = str(org["id"])
                break
        return default_org or first_org
    return None


def first_audience_id(claims: Dict[str, Any]) -> Optional[str]:
    aud = claims.get("aud")
    if isinstance(aud, list) and aud:
        return str(aud[0])
    if isinstance(aud, str):
        return aud
    return None


def normalize_expires_for_sub2api(*values: Any) -> str:
    for value in values:
        if value is None or value == "":
            continue
        if isinstance(value, str) and not re.fullmatch(r"\d+(\.\d+)?", value.strip()):
            # Preserve source ISO timezone string when it is already usable.
            return value.strip()
        epoch = iso_to_epoch(value)
        if epoch is not None:
            return epoch_to_iso_z(epoch, ms=False) or ""
    return ""


def make_sub2api_account(src: Dict[str, Any], template: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    token = find_access_token(src)
    if not token:
        return None

    cred_src = src.get("credentials") if isinstance(src.get("credentials"), dict) else {}
    extra_src = src.get("extra") if isinstance(src.get("extra"), dict) else {}
    claims = decode_jwt_payload(token)
    id_token_raw = first_nonempty(cred_src.get("id_token"), src.get("id_token"), "")
    id_claims = decode_jwt_payload(str(id_token_raw)) if id_token_raw else {}

    auth_claim = claims.get("https://api.openai.com/auth") if isinstance(claims.get("https://api.openai.com/auth"), dict) else {}
    profile_claim = claims.get("https://api.openai.com/profile") if isinstance(claims.get("https://api.openai.com/profile"), dict) else {}
    id_auth = id_claims.get("https://api.openai.com/auth") if isinstance(id_claims.get("https://api.openai.com/auth"), dict) else {}

    email = first_nonempty(
        cred_src.get("email"),
        extra_src.get("email"),
        src.get("email"),
        profile_claim.get("email"),
        id_claims.get("email"),
        claims.get("email"),
        src.get("name") if "@" in str(src.get("name", "")) else None,
    ) or "unknown@example.com"
    email = str(email)

    account_id = first_nonempty(
        cred_src.get("chatgpt_account_id"),
        src.get("chatgpt_account_id"),
        src.get("account_id"),
        auth_claim.get("chatgpt_account_id"),
        id_auth.get("chatgpt_account_id"),
        auth_claim.get("account_id"),
    ) or ""

    user_id = first_nonempty(
        cred_src.get("chatgpt_user_id"),
        src.get("chatgpt_user_id"),
        auth_claim.get("chatgpt_user_id"),
        id_auth.get("chatgpt_user_id"),
        auth_claim.get("user_id"),
        id_auth.get("user_id"),
        claims.get("sub"),
    ) or ""

    client_id = first_nonempty(
        cred_src.get("client_id"),
        src.get("client_id"),
        claims.get("client_id"),
        first_audience_id(id_claims),
    ) or ""

    organization_id = first_nonempty(
        cred_src.get("organization_id"),
        src.get("organization_id"),
        auth_claim.get("poid"),
        auth_claim.get("organization_id"),
        get_first_org_id(id_auth),
    ) or ""

    plan_type = first_nonempty(
        cred_src.get("plan_type"),
        src.get("plan_type"),
        auth_claim.get("chatgpt_plan_type"),
        id_auth.get("chatgpt_plan_type"),
        extra_src.get("plan_type"),
    ) or ""

    subscription_expires_at = first_nonempty(
        cred_src.get("subscription_expires_at"),
        src.get("subscription_expires_at"),
        id_auth.get("chatgpt_subscription_active_until"),
        auth_claim.get("chatgpt_subscription_active_until"),
    ) or ""

    expires_at = normalize_expires_for_sub2api(
        cred_src.get("expires_at"),
        src.get("expires_at"),
        src.get("expired"),
        claims.get("exp"),
    )

    token_version = first_nonempty(
        cred_src.get("_token_version"),
        src.get("_token_version"),
        int(claims.get("iat", 0)) * 1000 if claims.get("iat") else None,
        current_epoch_ms(),
    )

    model_mapping = first_nonempty(
        cred_src.get("model_mapping") if isinstance(cred_src.get("model_mapping"), dict) else None,
        src.get("model_mapping") if isinstance(src.get("model_mapping"), dict) else None,
        template.get("_model_mapping") if isinstance(template.get("_model_mapping"), dict) else None,
        DEFAULT_MODEL_MAPPING,
    )

    credentials: Dict[str, Any] = {
        "_token_version": token_version,
        "access_token": token,
        "chatgpt_account_id": str(account_id),
        "chatgpt_user_id": str(user_id),
        "client_id": str(client_id),
        "email": email,
        "expires_at": str(expires_at),
        "id_token": str(id_token_raw or ""),
        "model_mapping": dict(model_mapping),
        "organization_id": str(organization_id),
        "plan_type": str(plan_type),
        "refresh_token": str(first_nonempty(cred_src.get("refresh_token"), src.get("refresh_token"), "")),
        "subscription_expires_at": str(subscription_expires_at),
    }

    # Preserve additional non-empty credential fields after the canonical keys,
    # excluding legacy aliases and fields that conflict with the new schema.
    excluded = set(STANDARD_CREDENTIAL_KEYS) | {"token", "accessToken", "expires_in"}
    for k, v in cred_src.items():
        if k not in excluded and v is not None and v != "":
            credentials[k] = v

    extra_defaults = template.get("_extra_defaults") if isinstance(template.get("_extra_defaults"), dict) else DEFAULT_EXTRA_VALUES
    extra: Dict[str, Any] = {}
    for key in STANDARD_EXTRA_KEYS:
        if key == "email":
            extra[key] = email
        elif key in extra_src and extra_src[key] is not None and extra_src[key] != "":
            extra[key] = extra_src[key]
        elif key in extra_defaults:
            extra[key] = extra_defaults[key]
    for k, v in extra_src.items():
        if k not in extra and v is not None and v != "":
            extra[k] = v

    return {
        "name": str(src.get("name") or email),
        "platform": str(template.get("platform", "openai")),
        "type": str(template.get("type", "oauth")),
        "credentials": credentials,
        "extra": extra,
        "concurrency": template.get("concurrency", 10),
        "priority": template.get("priority", 1),
        "rate_multiplier": template.get("rate_multiplier", 1),
        "auto_pause_on_expired": template.get("auto_pause_on_expired", True),
    }

def convert_to_sub2api(input_path: Path, output_path: Path, reference_path: Optional[Path]) -> Dict[str, Any]:
    proxies, template = load_reference(reference_path)
    accounts: List[Dict[str, Any]] = []
    errors: List[str] = []
    seen_tokens = set()
    json_file_count = 0

    for label, data in iter_jsons_from_path(input_path):
        json_file_count += 1
        if isinstance(data, dict) and data.get("__parse_error__"):
            errors.append(f"{label}: {data['__parse_error__']}")
            continue
        for obj in iter_candidate_objects(data):
            try:
                acc = make_sub2api_account(obj, template)
                if not acc:
                    continue
                tok = acc["credentials"].get("access_token")
                if tok in seen_tokens:
                    continue
                seen_tokens.add(tok)
                accounts.append(acc)
            except Exception as exc:
                errors.append(f"{label}: {exc}")

    if not accounts:
        help_text = "No accounts with access_token were found in the input."
        if errors:
            help_text += " First parse errors: " + " | ".join(errors[:3])
        raise RuntimeError(help_text)

    exported = {
        "exported_at": now_iso_z(ms=False),
        "proxies": proxies,
        "accounts": accounts,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(exported, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "direction": "CPA/JSON -> sub2api",
        "output": str(output_path),
        "json_files": json_file_count,
        "accounts": len(accounts),
        "errors": errors,
        "error_count": len(errors),
    }


def iter_sub2api_exports(input_path: Path) -> Iterator[Tuple[str, Dict[str, Any]]]:
    for label, data in iter_jsons_from_path(input_path):
        if isinstance(data, dict) and data.get("__parse_error__"):
            continue
        if is_sub2api_export(data):
            yield label, data


def make_cpa_account(src: Dict[str, Any], cpa_type: str = "codex") -> Optional[Dict[str, Any]]:
    token = find_access_token(src)
    if not token:
        return None
    cred = src.get("credentials") if isinstance(src.get("credentials"), dict) else {}
    extra = src.get("extra") if isinstance(src.get("extra"), dict) else {}
    claims = decode_jwt_payload(token)
    auth_claim = claims.get("https://api.openai.com/auth") if isinstance(claims.get("https://api.openai.com/auth"), dict) else {}
    profile_claim = claims.get("https://api.openai.com/profile") if isinstance(claims.get("https://api.openai.com/profile"), dict) else {}

    email = first_nonempty(
        cred.get("email"),
        extra.get("email"),
        src.get("email"),
        profile_claim.get("email"),
        claims.get("email"),
        src.get("name") if "@" in str(src.get("name", "")) else None,
    ) or "unknown@example.com"
    email = str(email)

    account_id = first_nonempty(
        cred.get("chatgpt_account_id"),
        src.get("chatgpt_account_id"),
        src.get("account_id"),
        auth_claim.get("chatgpt_account_id"),
        auth_claim.get("account_id"),
    ) or ""

    expires_epoch = first_nonempty(
        src.get("expires_at") if isinstance(src.get("expires_at"), (int, float)) else None,
        iso_to_epoch(cred.get("expires_at")),
        iso_to_epoch(src.get("expires_at")),
        iso_to_epoch(src.get("expired")),
        claims.get("exp"),
    )
    expired = first_nonempty(
        normalize_iso_seconds(cred.get("expires_at")),
        normalize_iso_seconds(src.get("expired")),
        epoch_to_iso_z(expires_epoch, ms=False),
        now_iso_z(ms=False),
    )

    last_refresh = first_nonempty(
        normalize_iso_seconds(extra.get("last_refresh")),
        normalize_iso_seconds(cred.get("last_refresh")),
        normalize_iso_seconds(src.get("last_refresh")),
        now_iso_z(ms=False),
    )

    id_token = first_nonempty(cred.get("id_token"), src.get("id_token")) or ""
    refresh_token = first_nonempty(cred.get("refresh_token"), src.get("refresh_token")) or ""
    out_type = first_nonempty(cred.get("cpa_type"), src.get("cpa_type"), cpa_type, "codex")
    if out_type == "oauth":
        out_type = cpa_type or "codex"

    return {
        "access_token": token,
        "account_id": str(account_id),
        "email": email,
        "expired": str(expired),
        "id_token": str(id_token),
        "last_refresh": str(last_refresh),
        "refresh_token": str(refresh_token),
        "type": str(out_type),
    }


def convert_to_cpa(input_path: Path, output_path: Path, cpa_type: str = "codex") -> Dict[str, Any]:
    cpa_accounts: List[Dict[str, Any]] = []
    seen_tokens = set()
    export_count = 0
    errors: List[str] = []

    for label, export in iter_sub2api_exports(input_path):
        export_count += 1
        accounts = export.get("accounts") if isinstance(export.get("accounts"), list) else []
        for idx, item in enumerate(accounts, 1):
            if not isinstance(item, dict):
                continue
            try:
                cpa = make_cpa_account(item, cpa_type=cpa_type)
                if not cpa:
                    continue
                tok = cpa.get("access_token")
                if tok in seen_tokens:
                    continue
                seen_tokens.add(tok)
                cpa_accounts.append(cpa)
            except Exception as exc:
                errors.append(f"{label} account {idx}: {exc}")

    if not cpa_accounts:
        raise RuntimeError("No sub2api accounts were found. A sub2api file should contain exported_at/proxies/accounts.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    used_names: Dict[str, int] = {}
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for i, acc in enumerate(cpa_accounts, 1):
            base = safe_filename(acc.get("email") or f"account_{i}", f"account_{i}")
            count = used_names.get(base, 0)
            used_names[base] = count + 1
            if count:
                base = f"{base}_{count + 1}"
            filename = base + ".json"
            raw = json.dumps(acc, ensure_ascii=False, indent=2).encode("utf-8")
            zf.writestr(filename, raw)

    return {
        "direction": "sub2api -> CPA ZIP",
        "output": str(output_path),
        "sub2api_exports": export_count,
        "accounts": len(cpa_accounts),
        "errors": errors,
        "error_count": len(errors),
    }


def choose_file_gui() -> Tuple[Optional[str], Optional[Path], Optional[Path], str]:
    try:
        import tkinter as tk
        from tkinter import filedialog, simpledialog, messagebox
    except Exception:
        return None, None, None, "auto"

    root = tk.Tk()
    root.withdraw()
    root.update()

    messagebox.showinfo("CPA sub2api bridge", "Select input archive / JSON / folder. For URL, cancel file picker and paste URL next.")
    input_name = filedialog.askopenfilename(
        title="Select input archive or JSON",
        filetypes=[
            ("Archives / JSON", "*.zip *.zipx *.rar *.7z *.tar *.tgz *.gz *.bz2 *.xz *.json"),
            ("All files", "*.*"),
        ],
    )
    if not input_name:
        url = simpledialog.askstring("CPA sub2api bridge", "Paste sub2api URL, or leave blank to select a folder:")
        if url:
            source = url.strip().strip('"')
        else:
            folder = filedialog.askdirectory(title="Or select an input folder")
            if not folder:
                return None, None, None, "auto"
            source = folder
    else:
        source = input_name

    ref_name = filedialog.askopenfilename(
        title="Optional: reference sub2api JSON for CPA->sub2api only, or Cancel",
        filetypes=[("JSON", "*.json"), ("All files", "*.*")],
    )
    ref_path = Path(ref_name) if ref_name else None
    return source, ref_path, None, "auto"


def show_message(title: str, text: str, error: bool = False) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        if error:
            messagebox.showerror(title, text)
        else:
            messagebox.showinfo(title, text)
        root.destroy()
    except Exception:
        print(text)


def run_conversion(source: str, requested_direction: str, output_arg: Optional[str], reference_arg: Optional[str], cpa_type: str) -> Dict[str, Any]:
    with resolve_input_to_path(source) as (input_path, source_is_url):
        direction = requested_direction
        scan_info = ""
        if direction == "auto":
            direction, json_count, sub2api_count = detect_direction(input_path)
            scan_info = f"auto detected: {direction} (json files: {json_count}, sub2api exports: {sub2api_count})"

        output_path = Path(output_arg).expanduser() if output_arg else default_output_path(input_path, direction, source_is_url=source_is_url)
        reference_path = Path(reference_arg).expanduser() if reference_arg else None
        if direction == "cpa":
            result = convert_to_cpa(input_path, output_path, cpa_type=cpa_type)
        elif direction == "sub2api":
            result = convert_to_sub2api(input_path, output_path, reference_path)
        else:
            raise RuntimeError("Invalid direction. Use auto, sub2api, or cpa.")
        if scan_info:
            result["scan_info"] = scan_info
        result["external_extractor"] = find_external_extractor()[0] if find_external_extractor() else "none"
        return result


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Bridge converter for CPA packages and sub2api JSON/links. Outputs the v2 sub2api account schema.")
    parser.add_argument("input", nargs="?", help="Input archive, .json, folder, or http(s) link")
    parser.add_argument("-t", "--to", choices=["auto", "sub2api", "cpa"], default="auto", help="Output format. Default: auto")
    parser.add_argument("-r", "--reference", help="Optional reference sub2api JSON for CPA->sub2api")
    parser.add_argument("-o", "--output", help="Output path")
    parser.add_argument("--cpa-type", default="codex", help="CPA type field for sub2api->CPA. Default: codex")
    args = parser.parse_args(argv)

    gui_mode = False
    if not args.input:
        gui_mode = True
        source, ref_path, out_path, gui_direction = choose_file_gui()
        if not source:
            print("Cancelled.")
            return 1
        input_source = source
        requested_direction = gui_direction
        reference_arg = str(ref_path) if ref_path else None
        output_arg = str(out_path) if out_path else None
    else:
        input_source = args.input.strip().strip('"')
        requested_direction = args.to
        reference_arg = args.reference
        output_arg = args.output

    try:
        result = run_conversion(input_source, requested_direction, output_arg, reference_arg, args.cpa_type)
        lines = [
            "Conversion complete.",
            f"Direction: {result.get('direction')}",
            f"Output: {result.get('output')}",
            f"Accounts exported: {result.get('accounts')}",
            f"Parse/build errors: {result.get('error_count')}",
            f"External extractor: {result.get('external_extractor')}",
        ]
        if result.get("scan_info"):
            lines.insert(1, str(result["scan_info"]))
        if "json_files" in result:
            lines.insert(3, f"JSON files read: {result.get('json_files')}")
        if "sub2api_exports" in result:
            lines.insert(3, f"sub2api exports read: {result.get('sub2api_exports')}")
        text = "\n".join(lines)
        print(text)
        if result.get("errors"):
            print("\nFirst errors:")
            for err in result["errors"][:10]:
                print("- " + err)
        if gui_mode:
            show_message("CPA sub2api bridge", text, error=False)
        return 0
    except Exception as exc:
        text = f"Conversion failed:\n{exc}"
        print(text, file=sys.stderr)
        if gui_mode:
            show_message("CPA sub2api bridge", text, error=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
