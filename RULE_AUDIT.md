# RULE AUDIT – BASE_SL250_X5: Hyperliquid live vs Binance live (2026-10-22, giờ VN)

Lấy từ CODE + CONFIG thật, không lấy từ spec. So sánh: `/workspace/hlbot_live_x5` (HL) với `/workspace/hlbot_bnb_x5` (BNB).

**Bằng chứng toàn vẹn rule engine.** Các file rule giống nhau từng byte (sha256):
- strat.py c50f46ef26ca
- bbrank.py 93bfe5fa6cde
- ladder.py 785eb2ee222e
- dynuni.py 210f8166451a

Các file khác chỉ đổi phần kết nối sàn:
- **bnb_bot.py so với live_bot.py:** khác đúng 3 chỗ. (1) Docstring. (2) `import bnx as hlx` và biến `BNB_DRY_RUN`. (3) `CFGL.get('address')`.
- **livecore.py:** khác đúng 6 chỗ. Import bnx; biến `BNB_KILL`; mức tối thiểu theo từng coin `min_notional(c)` thay cho hằng số 10$ của HL (2 chỗ); `fills_since(...,c)` (2 chỗ, vì Binance bắt buộc truyền symbol).
- **config_live.json:** mọi khóa giống nhau, trừ khóa `live`, trong đó chỉ đổi 2 thứ. Địa chỉ ví HL được thay bằng `exchange: binance_usdm`. `allowed_abstractions` đổi từ `unifiedAccount` thành `binanceUSDM`, tức tài khoản phải ở one-way + single-asset.

| Rule | HL live BASE_SL250_X5 | Binance build | Giống/Khác | Lý do nếu khác |
|---|---|---|---|---|
| Shortlist: KL 24h | ≥ 3M$ (`dayNtlVlm`) | ≥ 3M$ (`quoteVolume` 24h USDT) | Giống (ngưỡng) | Nguồn số liệu là của sàn tương ứng |
| Shortlist: OI | ≥ 5M$ (OI × mark) | ≥ 5M$ (openInterest × markPrice) | Giống | |
| Shortlist: lịch sử | ≥ 300 nến 4h, ≥ 90 nến ngày | như HL | Giống | |
| Điểm tôn trọng BB 4h (90 ngày) | ≥ 0.75 | ≥ 0.75 | Giống | |
| Ổn định 1D | r30 < 1.8, r60 < 1.8, \|chg14\| < 30%, bwr < 2.0 | như HL | Giống | |
| Đòn bẩy tối đa của coin | ≥ 5 (meta HL) | ≥ 5 (leverageBracket) | Giống | Khi không có key thì giả định 20 (chỉ dry-run) |
| Rule nhóm BASE | OI ≥ 25M, KL ≥ 5M, ATR ≤ 8%, tối đa 15 coin | như HL | Giống | |
| Loại BTC/ETH | excluded_coins [BTC, ETH] | như HL | Giống | BTC chỉ dùng làm bộ lọc thị trường |
| Quét lại shortlist | mỗi 15 phút (+90s sau khi nến 15m đóng), sau khi đóng lệnh, lúc khởi động, làm mới 24h | như HL | Giống | |
| Bộ lọc BTC 1D/1W | long bị chặn khi 1D đóng dưới dải dưới và 1W ở nửa dưới (short ngược lại) | như HL | Giống | |
| BTC ngắn hạn | 4×15m ≤ −1.5%, 1h ≤ −1.5%, dưới dải 1h + dải mở rộng + volume ×1.5 | như HL | Giống | |
| Kích hoạt LONG | giá ≤ dải dưới BB 4h của nến đã đóng, kèm đủ bộ rule | như HL | Giống | |
| Kích hoạt SHORT | giá ≥ dải trên BB 4h, kèm đủ bộ rule | như HL | Giống | |
| Short Path A | điểm dải trên ≥ 0.78, tỉ lệ phá thật ≤ 0.10, vượt ≥6% ≤ 0.25, r30 ≤ ×1.5; không short khi nến 4h đóng trên dải trên | như HL | Giống | |
| Short Path B | nến 4h đóng dưới dải dưới, coin nằm trong shortlist L hoặc S | như HL | Giống | gate_adds = false |
| Độ dốc MA20 4h | long không giảm quá 3%/10 nến (short ngược lại) | như HL | Giống | |
| Cổng lệnh thứ 2 | BTC thuận lợi, lệnh 1 lãi ≥ 0$, MA20 dốc ≥ 0 | như HL | Giống | |
| Số vị thế tối đa | 2, không mở ngược chiều trên cùng coin | 2 | Giống | |
| Thang ký quỹ | 750/250/250/250 × vốn thật/5000 | như HL | Giống | |
| Điều kiện nhồi | mỗi bước cách giá ≥ 1.5% (add_step), qua đủ bộ rule | như HL | Giống | |
| Giới hạn 30% | ký quỹ ≤ 30%/vị thế; chặn notional 0.30 × vốn × 5 × 1.03 | như HL | Giống | |
| Ngưng nhồi khi lỗ | cảnh báo −250 × hệ số (= −190 ở vốn 3800) | như HL | Giống | |
| SL | CỐ ĐỊNH 250$ (không scale), là lệnh stop chờ sẵn trên sàn | như HL | Giống | Binance: algo STOP_MARKET closePosition, kích hoạt theo MARK_PRICE (như trigger HL) |
| Khóa lãi theo mốc | mốc 30$ × hệ số, khóa ≥ 2/3 | như HL | Giống | |
| Hòa vốn | lãi ≥ 40$ × hệ số → SL về hòa vốn | như HL | Giống | |
| Thoát theo điểm 15m | ≤ −3 hai nến, chỉ sau khi chạm dải giữa 4h; ≤ −4 khi lãi < 60$ × hệ số; xét khi lãi > 8$ × hệ số | như HL | Giống | |
| Trail 15m | lãi đỉnh 80$ → giữ 30%; 150$ → giữ 50% (× hệ số) | như HL | Giống | |
| TP dải đối diện 1h | chỉ có ở nhánh BookT1H (G6); nhánh BASE không dùng | như HL | Giống | Code giống hệt |
| Scale lại theo vốn | rescale_config mỗi lần khởi động theo vốn thật; không có chốt vốn tối thiểu | như HL (ví USDT Futures) | Giống | |
| Lệnh dưới mức tối thiểu | BỎ QUA, không tăng size (HL: 10$) | BỎ QUA, không tăng size (Binance: MIN_NOTIONAL từng coin, thường 5$; LINK/ETH 20$, BTC 50$; cả minQty) | Giống (cách xử lý) | Mức tối thiểu khác nhau là do sàn |
| Đòn bẩy | x5 | x5 (POST /fapi/v1/leverage) | Giống | |
| Chế độ ký quỹ | isolated | ISOLATED (POST /fapi/v1/marginType) | Giống | |
| Lệnh vào/nhồi/đóng | IOC limit, trượt giá tối đa 0.3% (đóng 1%/2%/5%) | LIMIT IOC, cùng mức trượt | Giống | Là lệnh market có chặn giá, giống HL |
| Phí mô hình | fee_taker 0.045% | 0.045% (không đổi) | Giống | PnL thật lấy từ fill của sàn (Binance taker khoảng 0.05%) |
| Phát hiện sai chế độ tài khoản | HL: phải là unifiedAccount | Binance: phải là one-way + single-asset | Tương đương | Yêu cầu tương thích của sàn |

**Sửa thêm một lỗi crash, không đổi rule.** Ở bnb_bot.py dòng 538, phần tính % sụt giảm (chỉ dùng cho cảnh báo) có chia cho `book.peak`. Khi vốn bằng 0 thì phép chia này báo ZeroDivisionError làm bot tự thoát rồi khởi động lại liên tục. Em đã chặn: khi `peak == 0` thì lấy dd = 0. Dòng này không ảnh hưởng đến việc vào hay ra lệnh. Bản HL cũng có dòng y hệt nhưng chưa gặp lỗi, vì vốn ở đó khoảng 7$.

Unit test chạy trên box: 41/41 OK.
- test_live: 21 test rule/engine dùng sàn giả lập.
- test_x5: 4 test.
- test_pnl_monitor: 2 test.
- test_bnx: 16 test cho adapter Binance (làm tròn giá/size, bỏ qua lệnh dưới min notional và minQty, lệnh stop algo, IOC và chặn notional, ISOLATED + x5, hủy lệnh algo/thường, đọc key mà không in ra).
