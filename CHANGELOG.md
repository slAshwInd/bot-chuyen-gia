# BaseAster – Binance USDT-M (ASTER, x5)

## 2026-11-04 – cập nhật chiến lược (gói anh Vien gửi 04/11)
- Bước 1: chạm dải BB dùng high/low nến 1m (2 nến gần nhất) thay vì chỉ giá hiện tại.
- Ngưỡng BE/LOCK/TRAIL/thoát 15m tính theo % equity (pct_thresholds, ref 1000$), tính lại khi không có vị thế.
- Vòng lặp chính 60s -> 15s (cycle_s).
- force_universe (ASTER): tắt quét shortlist toàn thị trường; max leverage lấy trực tiếp từ Binance.
- Log lệnh bị chặn lặp lại mỗi 15 phút; Telegram /pnl v2, /action gồm lệnh bị chặn, push cảnh báo.
- Bản cài trên PC giữ chế độ PAPER: PAPER_ONLY.flag ép DRY-RUN + tài khoản ảo (bnb_bot.py, bnx.py).

Khóa API KHÔNG nằm trong repo (secrets/ bị .gitignore).
