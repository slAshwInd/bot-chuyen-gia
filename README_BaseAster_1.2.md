# BaseAster 1.2

**Nguồn gốc:** đổi tên và tổng hợp từ **BaseAster 1.2.1** (bản cập nhật mới nhất, `BaseAster_1.2.1.tar.gz`, SHA256 `150bcf3d20f4a93e2820d19c6a48d524285bee18dcf4f13e188eaf787ca2874c`, cây `bot-chuyen-gia_20261106_084556`).
Toàn bộ file Python, config JSON, launcher (.ps1/.bat), `ref/`, `tests/`, `README.md`, `RULE_AUDIT.md`, `requirements.txt`, `.gitignore` **giống hệt từng byte** với 1.2.1. Không có thay đổi logic. Không chứa thay đổi của 1.2.2 (sửa khởi động rescale, hook trail 80%...).

Chỉ thêm: `VERSION.txt`, file này, `PROVENANCE_BaseAster_1.2.md`, `MANIFEST_BaseAster_1.2.txt`, `SHA256_BaseAster_1.2.txt`, và `secrets/*.example` (placeholder, lấy từ cây nguồn 1.2.1; tarball 1.2.1 không chứa thư mục này dù README.md có hướng dẫn copy).

## Nội dung chiến lược (theo config_live.json của 1.2.1)
- Chu kỳ quét chính **5 giây** (`cycle_s: 5`).
- **LONG bước 3 cũ tắt** (`step3.long_enabled: false`), thay bằng **Bắt đáy LONG (BatDay, HTML v5)**: `batday.enabled: true`, `dry_run: false`, coin ASTER, 6 nến, giảm ≥3%, mom 0.7%, cách nhau 12h, lọc BB4h ≤15%, BTC 24h ≥ −8%, notional 4.9× vốn, TP 1.5%, SL 2.0%, giữ tối đa 6h, đòn bẩy 5.
- **SHORT bước 3 giữ nguyên** (`step3.enabled: true`).
- BASE: x5, SL SL250 (1250 tại vốn tham chiếu), thang/size theo `sizing`/`pct_thresholds` trong config (start_capital 1000). Chi tiết: `README.md`, `RULE_AUDIT.md`.

## Cài đặt & chạy (Windows)
1. Python ≥ 3.12; `python -m venv .venv` rồi `.venv\Scripts\pip install -r requirements.txt` (numpy, pandas, requests, matplotlib).
2. Tạo secrets từ `secrets\*.example` (hoặc `set_key.bat`). API key tắt quyền rút tiền, giới hạn IP. Không bao giờ đưa secrets thật vào gói.
3. `install_task_bnb.ps1` (Scheduled Task) → `start_bnb.bat`. Cần `ref/config_paper.json` (có sẵn) cho `make_live_config.py`.
4. Tài khoản Futures phải có số dư USDT; không có số dư thì bot chỉ chờ.

## Hạn chế kế thừa (không sửa trong bản đổi tên)
- 5 test thất bại kế thừa từ 1.2.1: `test_x5.T_x5.test_sizing_3800` (ERROR), `test_x5.T_x5.test_live_add`, `test_x5.T_x5_small.test_dynamic_cap_follows_equity`, `test_x5.T_x5_small.test_tiny_equity_skips_cleanly`, `test_live.T_dry.test_entry_payloads_and_sizing`.
- Ngưỡng % (pct_thresholds) phụ thuộc CAP sau rescale.
- Lộ trình khởi động của 1.2.1 (rescale → make_live_config) chưa có các bảo vệ mà 1.2.2 bổ sung.
- `ladder.py` là script nghiên cứu, import `backtest.py` không có trong gói.
- `tests/test_batday_exec.py` (kiểm SHORT không đổi) so với `/tmp/base/step3.py` — baseline ngoài gói.
- Đổi tên không chứng nhận tính đúng runtime; không chạy test/backtest mới cho bản này.
