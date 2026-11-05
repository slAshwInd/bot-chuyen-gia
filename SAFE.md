# BaseAster SAFE: bản an toàn cho vốn 8000$

Bản này giữ nguyên toàn bộ rule vào và ra lệnh của bot live, gồm BB 4h, lọc BTC, lọc 0.7%, Bước 3, hòa vốn, khóa lãi, trail 15m và chốt theo điểm 15m. Chỉ có 2 lớp được thêm vào:

1. **Khối lượng theo rủi ro.** Một lần dính SL chỉ mất `risk_pct`% vốn (mặc định **3%**), thay cho 25% như bot live.
2. **Cầu dao vào lệnh.** Bot ngừng vào lệnh mới sau chuỗi thua, khi lỗ trong ngày chạm ngưỡng hoặc khi vốn sụt sâu. Vị thế đang mở vẫn được quản lý bình thường.

## 1. Vì sao bot live chưa an toàn để đánh vốn lớn

| | Bot live (ở 8000$) | SAFE 3% (ở 8000$) |
|---|---|---|
| Ký quỹ / đòn bẩy | 98% vốn, x5 | 11.8% vốn, x5 isolated |
| Notional | 39 200$ (4.9x vốn) | 4 704$ (0.59x vốn) |
| Khoảng cách giá tới SL | ~5.1% | **~5.1% (giữ nguyên)** |
| Mất khi dính 1 SL | **2 000$ (25%)** | **240$ (3%)** |
| Số SL liên tiếp để mất 30% vốn | 2 | 12 |
| Số SL liên tiếp để mất 50% vốn | 3 | 23 |
| Hòa vốn khi giá đi thuận | ~0.82% (*) | ~0.82% |

(*) Ở bot live hiện tại, khi vốn khác 1000$, ngưỡng này **bị sai** (xem mục 4).

Mean reversion BB thắng nhiều lần nhỏ nhưng thỉnh thoảng gặp trend kéo dài. Ở mức 25% vốn mỗi SL, chỉ 2–3 lần SL liền nhau đã đủ làm vốn khó gượng lại. SAFE **giữ nguyên mọi mức giá**: giá SL, giá hòa vốn, giá khóa lãi và trail đều đặt y như bot live. Phần nhân hệ số `k = risk_pct / 25` gồm khối lượng và mọi tham số tính bằng $. Kết quả là tỉ lệ thắng thua và cách bot ra vào không đổi, chỉ biên độ lãi lỗ tính theo % vốn nhỏ lại theo `k`.

> Đánh đổi: lãi theo % vốn cũng nhỏ lại theo cùng tỉ lệ. Với 8000$ và risk 3%, khối lượng mỗi lệnh (~4 700$ notional) gần bằng khối lượng bot live đang đánh trên ~1000$ vốn. Nói cách khác, khối lượng giữ như cũ, nhưng một lần SL chỉ còn mất 3% thay vì 25%.

Chọn mức rủi ro:

| risk_pct | Notional ở 8000$ | Mất mỗi SL | SL liên tiếp để mất 30% |
|---|---|---|---|
| 2% | 3 136$ | 160$ | 18 |
| **3% (mặc định)** | **4 704$** | **240$** | **12** |
| 4% | 6 272$ | 320$ | 9 |
| 5% | 7 840$ | 400$ | 7 |

Nên giữ mức tối đa 5%.

## 2. Hạn chế số lần dính SL: cầu dao

Cầu dao chỉ chặn lệnh **vào mới**, áp dụng cho cả BaseAster và Bước 3. Nó không đóng hay sửa vị thế đang mở.

| Cầu dao | Mặc định | Lý do |
|---|---|---|
| Nghỉ cùng chiều sau SL | 12h | Bot live vào lại ngay sau SL. Khi giá đang chạy một mạch, lần chạm dải tiếp theo thường cũng SL, nên nghỉ để tránh SL kép. |
| Thua SL liên tiếp ≥ 2 | Nghỉ cả 2 chiều 48h | Thị trường đang không hợp mean reversion. |
| Lỗ trong ngày ≥ 6% vốn đầu ngày (giờ VN) | Nghỉ tới hết ngày | Chặn ngày xấu. |
| Sụt từ đỉnh vốn ≥ 15% | Tạo `SAFE_HALT.flag` và dừng hẳn | Cần người xem lại, rồi chạy `python safe.py reset`. |

Mỗi lần bị chặn, bot ghi sự kiện `ENTRY_BLOCKED_RULES` với lý do `SAFE: ...` và gửi Telegram (🛡).

**Lưu ý thật:** không có cách nào loại bỏ SL hoàn toàn mà vẫn giữ rule. Muốn bớt SL thì phải chặn bớt lệnh, và cũng sẽ mất một số lệnh thắng. Các ngưỡng 12h/48h/6%/15% là mặc định hợp lý, **chưa được backtest** trong repo này vì repo không có dữ liệu nến. Anh chạy `safe_replay.py` trên lịch sử lệnh thật (mục 5) để xem cầu dao đã bỏ những lệnh nào, rồi chỉnh trong `safe_profile.json`.

## 3. Cài đặt (tách hẳn khỏi bot live)

1. **Tài khoản riêng:** tạo Binance **sub-account** cho 8000$, ở chế độ one-way và single-asset. Tạo API key riêng, tắt Withdrawals và whitelist IP. Không chạy chung tài khoản với bot live, vì cả hai cùng đánh ASTER sẽ giành nhau vị thế.
2. **Máy riêng:** dùng VPS hoặc máy Windows khác. Các script dùng tên task cố định (`HLBot BNB X5`, `HLBot_Monitor`, `HLBot_TG_Poller`), nên cài 2 bot trên cùng một máy thì task của bản SAFE sẽ **ghi đè task của bot live**, và nút khẩn cấp sẽ tắt nhầm bot.
3. Clone repo vào thư mục mới, cài như README, rồi:
   ```powershell
   copy safe_profile.example.json safe_profile.json
   ```
   (`safe_profile.json` đã có trong `.gitignore` nên không bao giờ bị commit, và thư mục bot live không bao giờ tự thành SAFE.)
4. Dùng Telegram bot hoặc chat riêng để phân biệt tin nhắn.
5. Chạy thử: `run_bnb.ps1 -Dry`. Log khởi động phải có dòng `SAFE_ON {...}`. Lệnh `python safe.py show 8000` in ra bảng số liệu.
6. Bật LIVE bằng `start_bnb.bat` như bot thường. Mỗi lần khởi động, `rescale_config.py` đọc vốn thật rồi tự áp lớp SAFE.

Muốn đổi `risk_pct`: sửa `safe_profile.json` rồi khởi động lại, config sẽ được tạo lại. Các ngưỡng cầu dao được đọc lại mỗi lần bot khởi động.

## 4. Lỗi của bot live khi tăng vốn (cần sửa trước khi nạp thêm tiền vào bot live)

`make_live_config.py` tính `pct_thresholds` = (giá trị $ đã nhân vốn/5000) / 10. Công thức này **chỉ đúng khi vốn ≈ 1000$**. Ở 8000$, các ngưỡng hòa vốn, khóa lãi, trail và ngưỡng xét chốt 15m tăng gấp 8 lần. Hòa vốn khi đó cần giá chạy ~6.5% thay vì ~0.82%, nên gần như không bao giờ dời SL về hòa vốn. Ở vốn hiện tại ~1061$, ngưỡng chỉ lệch khoảng 6%.

Bản SAFE đã dùng công thức đúng (`paper$ / 5000 × 100`, không phụ thuộc vốn). Test `tests/test_safe.py::test_live_pct_bug_documented` chứng minh lỗi này. Code của bot live **chưa được sửa** để không đổi hành vi bot đang chạy khi chưa được duyệt.

## 5. Đo trên lịch sử lệnh thật

```powershell
.venv\Scripts\python.exe safe_replay.py                      # data/trades_side_live.csv + data/step3_trades.csv, vốn safe 8000, risk 3%
.venv\Scripts\python.exe safe_replay.py - 1000 8000 4        # đổi vốn live đầu / vốn safe / risk
```
Kết quả gồm số lệnh, số SL, lãi/lỗ, mức sụt tối đa của live so với SAFE, cùng danh sách lệnh bị cầu dao bỏ qua (chi tiết ở `data/safe_replay.csv`). Giới hạn của công cụ: nó không sinh được lệnh mới có thể xuất hiện khi một lệnh bị bỏ. Muốn kết quả chính xác hơn thì cần replay nến đầy đủ trên máy nghiên cứu.

## File

| File | Vai trò |
|---|---|
| `safe.py` | overlay size (`overlay`), cầu dao (`Guard`), CLI `show` / `reset` |
| `safe_profile.example.json` | mẫu cấu hình, copy thành `safe_profile.json` ở thư mục bot SAFE |
| `safe_replay.py` | chạy lại lịch sử lệnh qua luật SAFE |
| `make_live_config.py`, `rescale_config.py` | tự áp SAFE khi có `safe_profile.json` |
| `bnb_bot.py`, `step3.py` | nối cầu dao vào lệnh vào BaseAster và Bước 3 (chỉ khi config có khối `safe`) |
| `tests/test_safe.py` | 16 test: mức giá giữ nguyên, size đúng, lỗi pct của live, cầu dao |
