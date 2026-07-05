CPA <-> sub2api bridge v2.1

Use RUN_ME_BRIDGE.cmd by dragging your file/folder/archive onto it.
Use RUN_MANUAL_BRIDGE.cmd if drag-and-drop fails or if your input is a URL.

This v2.1 package fixes Windows CMD error code 9009 caused by bad Python command detection.
The launcher now tests each Python command by actually running a short Python snippet before using it.

Input supported:
- CPA archive/folder/single JSON -> sub2api JSON
- sub2api JSON/link -> CPA ZIP
- zip, tar, tar.gz, tgz, tar.bz2, tbz2, tar.xz, txz, gz, bz2, xz, json, folder
- rar/7z/zipx/cab if 7-Zip or WinRAR is installed

The sub2api output format follows the provided v2 schema sample:
- top-level: exported_at, proxies, accounts
- account fields include credentials, extra, concurrency, priority, rate_multiplier, auto_pause_on_expired

If it still fails, run TEST_PYTHON.cmd and send the screenshot/output.
