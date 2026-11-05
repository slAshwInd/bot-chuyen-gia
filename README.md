# BaseAster – bot Binance USDT-M Futures (ASTER x5)

Bot giao dịch tự động **ASTERUSDT** trên Binance USDⓈ-M Futures, đòn bẩy **x5**, tối đa 1 vị thế.

- **BASE (Bước 1–2):** vào lệnh đảo chiều khi giá chạm biên Bollinger Band 4h (long/short), nhồi lệnh theo thang, SL cứng đặt sẵn trên sàn, dời SL về hoà vốn rồi khoá lãi theo % equity, thoát theo chấm điểm chart 15m.
- **Bước 3:** quy tắc vào lệnh bổ sung khi Bước 2 không thoả (xem `step3.py`, `RULE_AUDIT.md`).
- **Lọc BTC:** chặn chiều long/short theo xu hướng BTC.
- **Monitor (`monitor_bnb.py`):** theo dõi `bot_live.log`, cảnh báo Telegram ngay khi bot ngừng, exception, API key/IP bị từ chối (-2015), gọi API thất bại liên tục, halted / mất đồng bộ, **IP public ngoài danh sách trust**. Ghi log phiên 5 phút, gửi tổng hợp định kỳ.
- **Telegram poller (`tg_live_poller.py`):** menu lệnh chỉ đọc + quản lý danh sách IP trust.

> ⚠️ **Cảnh báo rủi ro:** đây là bot giao dịch **tiền thật**, có đòn bẩy. Mỗi lần dính SL có thể mất khoảng 25% vốn. Kết quả quá khứ/backtest không đảm bảo lợi nhuận. Chỉ dùng tiền chấp nhận mất. API key **phải tắt quyền rút tiền (Withdrawals)** và **giới hạn IP**.

## Yêu cầu
- Windows 10/11, Python **≥ 3.12**
- Tài khoản Binance có ví USDⓈ-M Futures (số dư USDT > 0), chế độ vị thế **one-way**
- (Tuỳ chọn) bot Telegram tạo bằng @BotFather

## Cài đặt
```powershell
cd <thu_muc_bot>
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```
1. **Secrets** – tạo từ file mẫu (các file thật đã bị `.gitignore`, không bao giờ commit):
   ```powershell
   copy secrets\binance_api_key.txt.example    secrets\binance_api_key.txt
   copy secrets\binance_api_secret.txt.example secrets\binance_api_secret.txt
   copy secrets\telegram.env.example           secrets\telegram.env
   ```
   Sửa nội dung (mỗi file key/secret 1 dòng, không xuống dòng cuối), hoặc chạy `set_key.bat` / `set_key.ps1` để nhập key.
   `telegram.env`: `TELEGRAM_BOT_TOKEN=...` và `TELEGRAM_CHAT_ID=...` (lấy chat id qua `https://api.telegram.org/bot<token>/getUpdates`).
2. **Cấu hình monitor:** `copy monitor_config.example.json monitor_config.json`, điền `trusted_ips` bằng IP thật của mạng nhà.
3. **Whitelist IP trên Binance:** API Management → Edit restrictions → thêm các IP trong `trusted_ips`, bật *Enable Futures*, tắt *Withdrawals*.
4. **Vốn đầu để tính PNL:** sửa `start_capital` trong `config_live.json` theo số dư thật (bản mẫu để 1000).
5. **Đăng ký task chạy ẩn:** `powershell -ExecutionPolicy Bypass -File install_task_bnb.ps1` (tạo `HLBot BNB X5`, `HLBot_TG_Poller`, `HLBot_Monitor` theo thư mục hiện tại; chạy lại khi chuyển thư mục).

## Sử dụng
| Việc | Lệnh |
|---|---|
| Kiểm tra key, số dư, chế độ vị thế (không đặt lệnh) | `.venv\Scripts\python.exe selftest_bnb.py` → phải ra `READY` |
| Chạy thử không gửi lệnh | `powershell -ExecutionPolicy Bypass -File run_bnb.ps1 -Dry` |
| Bật bot **LIVE** | `start_bnb.bat` (chỉ chạy 1 lần, không chạy lại khi bot đang chạy) |
| Khẩn cấp: đóng vị thế + dừng | `emergency_bnb.bat` |
| Dashboard | `dashboard_bnb.bat` |

### Lệnh Telegram (chỉ nhận từ `TELEGRAM_CHAT_ID` đã cấu hình, không đặt lệnh giao dịch)
| Lệnh | Ý nghĩa |
|---|---|
| `/status` | Bot có chạy không, task, tiến trình, LOOP cuối, equity, vị thế, lỗi cuối |
| `/pnl` | Vốn, số lệnh, PNL |
| `/action` | Hành động gần nhất (vào/thoát/bị chặn) |
| `/summary [phút]` | Tổng hợp N phút gần nhất (mặc định 60) |
| `/log` | Lỗi gần nhất trong log |
| `/ip` | IP public hiện tại + danh sách IP trust |
| `/trustip <ip>` | Yêu cầu thêm IP vào danh sách trust → gõ `/confirm` trong 2 phút |
| `/untrustip <ip>` | Yêu cầu xoá IP khỏi danh sách → `/confirm` |
| `/confirm`, `/cancel` | Xác nhận / huỷ yêu cầu IP |
| `/help` | Danh sách lệnh |

### Luồng IP trust
1. Monitor kiểm tra IP public mỗi `ip_check_min` phút và đọc `request ip` trong lỗi -2015 của Binance.
2. Đổi qua lại giữa các IP trong `trusted_ips` → không cảnh báo.
3. IP lạ → cảnh báo ngay: thêm IP đó vào whitelist trên Binance, rồi `/trustip <ip>` + `/confirm`.
4. Quay về IP trust → tin "ĐÃ PHỤC HỒI".
Các lệnh IP chỉ sửa `monitor_config.json`, không đụng tới giao dịch.

### `monitor_config.json` (đọc lại mỗi vòng, không cần khởi động lại)
| Trường | Ý nghĩa |
|---|---|
| `summary_interval_min` | Chu kỳ gửi tổng hợp Telegram (phút, 0 = tắt) |
| `session_min` | Độ dài phiên ghi log `logs/session_YYYYMMDD.txt` |
| `send_session_to_telegram` | Gửi cả tổng hợp từng phiên lên Telegram |
| `alert_cooldown_min` | Nhắc lại cùng một lỗi sau N phút |
| `stale_loop_s` | Quá N giây không có LOOP mới → báo bot ngừng |
| `ip_check_min`, `ip_url` | Chu kỳ và URL kiểm tra IP public |
| `trusted_ips` | Danh sách IP đã whitelist trên Binance |
| `api_fail_threshold`, `api_fail_window_min` | Số lỗi API trong cửa sổ phút để cảnh báo |
| `proc_check_s` | Chu kỳ kiểm tra task/tiến trình |

## Bảo mật
- `.gitignore` loại `secrets/*` (trừ `*.example`), `*.env`, `monitor_config.json`, state, log, `.venv`. **Giữ file `.gitignore`** để không lỡ commit key.
- Không dán API key/token vào chat hay issue.
