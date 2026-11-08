# TrendMR-1H — module chiến lược cho BTC/ETH/BNB trên nền tảng BaseAster (từ bản 1.3)

Module `trendmr.py` chạy **chung nền tảng** với BaseAster: cùng kết nối Binance (`hl.py`/`bnx.py`),
cùng lớp thực thi/sự kiện (`livecore` → `events_live.jsonl`), cùng kênh Telegram (`tg_live.send`),
cùng nhịp vòng lặp chính 5 giây và cùng `state_live.json` (mục `trendmr`). Nó là một module độc lập
kiểu BatDay/Step3: tự giữ trạng thái (`state/trendmr.json`) và sổ lệnh riêng (`data/trendmr_trades.csv`),
**không chạm vào Book, vị thế hay rule nào của BaseAster/BatDay/Step3**.

## Đảm bảo ASTER giữ nguyên

- `strat.py`, `livecore.py`, `batday.py`, `step3.py`, `bnx.py` **không sửa một dòng nào**.
- `bnb_bot.py` chỉ cộng thêm: import module, khởi tạo, gọi `tick()` mỗi vòng, ghi thêm mục trạng thái,
  và gộp toggle coin (mặc định tất cả bật → tập loại trừ không đổi).
- Toàn bộ test gốc cho kết quả **giống hệt baseline** trước khi sửa (80 test, cùng 3 lỗi môi trường có sẵn);
  17 test mới của TrendMR đều đạt.

## Bật / tắt từng cặp coin

Hai lớp công tắc, mặc định **bật cả 4 coin ASTER/BTC/ETH/BNB**:

1. `cfg["coins"][COIN]["enabled"]` — công tắc tổng toàn hệ thống: `false` = coin bị loại khỏi mọi
   lệnh vào mới (Book BaseAster + BatDay/Step3 + TrendMR đều tôn trọng); vị thế đang mở vẫn được quản lý/thoát bình thường.
2. `cfg["trendmr"]["coins"][COIN]` — coin nào chạy TrendMR. Mặc định BTC/ETH/BNB = true, **ASTER = false**
   vì ASTER thuộc BaseAster/BatDay/Step3 như hiện tại (bật ASTER ở đây chỉ khi muốn chạy thêm TrendMR cho ASTER).

## Vốn

`cfg["trendmr"]["capital_usdt"] = 5000` là vốn gốc của riêng module, chia đều cho các coin TrendMR đang bật
(mặc định 3 coin → sleeve ≈ 1.666,67$/coin), notional mỗi lệnh = sleeve × `notional_x` (1.0).
SHORT BNB hệ số 0,5. Kiểm soát rủi ro **theo đúng triết lý BaseAster**: KHÔNG có chặn giao dịch theo
ngày/chuỗi lỗ/drawdown (kể cả `cat_stop` của BaseAster cũng đang tắt — các ngưỡng của nó chỉ phát cảnh báo);
bảo vệ cứng nằm ở stop của từng vị thế. Các ngưỡng lỗ ngày −3%, 3 SL liên tiếp/coin, drawdown −12%
chỉ phát sự kiện `TRENDMR_ALERT` (ngày 1 lần, drawdown 1 lần cho tới khi hồi phục một nửa), không chặn lệnh.

## Chiến lược TrendMR-1H (tham số đã backtest 2021–2026, Binance)

Vào lệnh khi giá chạm biên Bollinger 1h (nến đóng gần nhất) **chỉ cùng chiều EMA100**: LONG khi đóng trên EMA
(mua nhịp giảm trong uptrend), SHORT khi đóng dưới EMA. Giữ 2 lọc gốc BaseAster: momentum 4h 0,7% và dốc MA20 4h ≤ 3%/10 nến.
Chỉ chạm đầu tiên của episode. Thoát: SL −1,5%; lãi tạm tính +0,6% → dời stop hoà vốn (×1,002/×0,998);
**khóa lãi theo mốc kiểu BaseAster (lock30)**: mỗi 1% lãi đỉnh → stop khóa ≥ 2/3 mốc đó; TP tại dải giữa BB 1h
(cập nhật theo nến đóng, chỉ khi còn phía lãi ≥ 0,3%). **Không thoát theo thời gian** (như BaseAster).
**Thoát phụ Book15 (từ 1.3.2)**: tại mỗi nến 15m đã đóng mới, nếu lãi tạm tính sau phí vào (tại giá đóng 15m)
> 8$ và giá đã chạm dải giữa BB 4h kể từ lúc vào (cổng C2) thì điểm triển vọng 15m ≤ −3 (≤ −4 khi lãi < 60$)
ở hai nến liên tiếp → đóng toàn bộ (`EXIT_15M`). TP dải giữa vẫn là chốt lời chính; thoát 15m là phụ.
Phí mô hình paper — đúng biểu Binance USDT-M futures hạng thường (maker 0,02% / taker 0,05%) và đúng ngữ nghĩa
từng loại lệnh như engine backtest: vào lệnh limit tại biên = maker; thoát TP limit tại dải giữa = **maker**;
mọi thoát kiểu market (stop SL/BE-TRAIL, EXIT_15M) = taker; trượt giá stop 0,05%. (Trước 2026-11-08 paper
tính taker cho cả TP — lệch cả Binance lẫn backtest, nay đã sửa, commit `5a01141`.) Funding chưa mô hình
hoá (như backtest) — xem ghi chú nghiệm thu.

### Đánh giá ưu điểm BaseAster và những gì đã chuyển giao (2026-11-08, có backtest đối chứng)

| Ưu điểm BaseAster | Chuyển giao vào TrendMR? | Bằng chứng |
|---|---|---|
| Khóa lãi theo mốc đỉnh (lock30, giữ 2/3) | **CÓ — áp dụng cả 3 coin** | BTC PF 1,51→1,56, OOS 1,37→1,48, stress OOS 1,00→1,10, DD −10,2→−9,2%; ETH 1,67→1,69 / OOS 1,52 / stress OOS 1,25; BNB 1,56→1,63 / stress 1,28 |
| Không thoát theo thời gian; giữ lệnh tới stop/mục tiêu | **CÓ — bỏ hẳn time-exit 120h** | Số liệu không đổi (thời gian giữ TB chỉ 2–4h, lệnh gần như không bao giờ chạm 120h) — bỏ cho đúng triết lý, không mất gì |
| Rủi ro = stop từng vị thế; ngưỡng ngày/DD chỉ cảnh báo, `cat_stop` tắt | **CÓ — thay toàn bộ chặn tự chế bằng `TRENDMR_ALERT`** | Đúng cấu hình đang chạy live của BaseAster (alert_warn/crit chỉ cảnh báo, dd_alert 25% chỉ ghi sự kiện) |
| Chốt 50% ở dải giữa + runner chạy tới biên đối diện (kiểu B) | **KHÔNG — đã test và loại** | PF rớt còn 1,23/1,29/1,18 (BTC/ETH/BNB), stress âm ở BTC/BNB; TP 100% ở biên đối diện còn tệ hơn (PF 0,80–0,93, DD −32…−70%): hình học 1h khác 4h — đoạn biên→giữa là phần ăn chắc, đoạn giữa→biên đối diện thường trả lại |
| Thoát theo điểm triển vọng 15m (Book15) | **CÓ — từ 1.3.2, biến thể V1 (thoát phụ, giữ TP dải giữa)** | Đã chạy mô hình 15m riêng (walker 15m, port nguyên văn score15, điểm vào giữ nguyên): V1 thắng V0 (1.3.1) ở cả 3 coin, OOS lẫn stress phí ×2 — OOS 2026 PF BTC 1,81→1,92 / ETH 1,99→2,02 / BNB 1,71→2,36, lãi ròng OOS +93/+90/+535$; thoát 15m chỉ kích hoạt ~8% lệnh. Bỏ TP dải giữa chỉ thoát bằng score15 (V2/V4) bị loại: PF tụt, stress OOS âm ở BTC (0,66) và ETH (0,90); ngưỡng $ chia theo sleeve (V1s) không thuyết phục → giữ ngưỡng $ nguyên văn. Chú ý: PF tuyệt đối của walker 15m (~3) cao hơn engine 1h vì cách xử lý thứ tự chạm trong giờ — chỉ chênh lệch V1−V0 mới là kết luận |
| Nhồi lệnh theo thang (ladder) | Không | Xung đột rule "chỉ chạm đầu tiên của episode"; chưa có bằng chứng ủng hộ |

### Tách riêng theo pair — chỉ khi bằng chứng cho thấy tốt hơn

Đã kiểm tra lại toàn bộ biến thể theo từng pair (đầy đủ / ngoài mẫu 2026 / stress phí gấp đôi) trước khi chốt:

| Pair | Bộ thống nhất (BB 2,0 · EMA100) | Biến thể | Kết luận |
|---|---|---|---|
| BTC | PF 1,51 · OOS 1,37 · stress OOS 1,00 | 2,5 / EMA200 đều kém hơn mọi tiêu chí | **Giữ thống nhất** |
| ETH | PF 1,67 · OOS 1,45 · stress OOS 1,18 | BB 2,5: PF đầy đủ 1,79 nhưng OOS sập 1,11, stress OOS 0,85 → dấu hiệu overfit | **Giữ thống nhất** |
| BNB | PF 1,43 · OOS 1,27 · stress OOS 0,97 · DD −14,0% | **BB 2,5: PF 1,56 · OOS 1,35 · stress 1,21 / OOS 1,05 · DD −10,3%** — thắng mọi tiêu chí | **Tách riêng: `coin_params.BNB.bb_k = 2.5`** |

Vệ tinh đi kèm cũng đã kiểm tra và **loại** vì không chứng minh được tốt hơn: BTC + BatDay-5
(PF gộp 1,43 < 1,51, DD sâu hơn −12,8% so với −10,2%, stress gần như không đổi) và BNB + EMA-TrendLock 0,25×
(cải thiện OOS/stress nhưng trùng lặp với lợi ích của BB 2,5 và thêm một engine thoát lệnh — giữ làm phương án dự phòng, ghi trong SPECS).

Muốn thêm override cho pair khác: sửa `cfg["trendmr"]["coin_params"]` (vd `{"ETH": {"ema_n": 100}}`) — không cần sửa code.

## Paper / forward test

Mặc định `dry_run = true`: khớp mô phỏng tại giá biên, ghi đủ sổ lệnh (giờ vào/ra, giá, khối lượng, phí, PnL)
vào `data/trendmr_trades.csv`. Hai cách chạy:

- **Trong bnb_bot** (môi trường runtime đầy đủ): module tự chạy theo vòng lặp chính, sự kiện vào
  `events_live.jsonl`, thông báo qua Telegram như mọi module khác, trạng thái ở `state_live.json → trendmr`.
- **Độc lập (headless)** cho máy không vào được FAPI/không có API key: `python trendmr_paper.py --runtime <thư mục runtime>`
  — dùng đúng module này, dữ liệu Binance public (spot `data-api.binance.vision`, cùng định dạng `hl.candles`),
  ghi `events.jsonl` + `heartbeat.json` trong thư mục runtime (ngoài cây source).

## Sizing 1.3.3 — ngân sách tổng notional lãi kép (G2) + van tiết lưu DD

Từ 1.3.3 (user duyệt 2026-11-08, commit `fe2bf1c`), sizing sleeve tĩnh được thay bằng mô hình ngân sách:
tại thời điểm vào lệnh, **ngân sách = equity đã chốt (vốn gốc + PnL đã chốt) × `gross_lev` (5)** — tách riêng,
không gồm ASTER; ký quỹ tối đa (= equity hiện tại ở isolated x5) tăng theo lãi kép, lỗ thì co lại. Vị thế mở
thứ 1/2/3 lấy lần lượt **50% / 30% / 20%** ngân sách (cách chia theo slot của 1.3.3; từ 1.3.4 đổi sang
chia theo ngân sách còn lại — xem dưới); hệ số SHORT BNB 0,5 nhân sau cùng.
**Van tiết lưu DD**: equity đã chốt dưới đỉnh của chính nó ≥ 12% → hệ số ngân sách ×0,5; ≥ 20% → ×0,25
(chỉ giảm size lệnh mới, không chặn lệnh; backtest: DD toàn kỳ −30,2% → −22,0%). Nghiên cứu nền:
`sizing_compare.py`, `sizing_gross.py`, `sizing_gross2.py`, `risk_mc.py` trong thư mục research đi kèm
(Monte Carlo theo tỉ lệ thắng/thua dự kiến 72,5% / +0,173% mỗi lệnh: DD điển hình −28,5%, P(DD ≥ 70%) ~0%;
rủi ro cháy thật là edge mòn, không phải một lệnh đơn — SL −1,5% luôn cắt trước liquidation x5).

## Sizing 1.3.4 — chia theo NGÂN SÁCH CÒN LẠI (giữ gross x5)

Từ 1.3.4 (user duyệt 2026-11-08, nghiên cứu `leverage_alloc_eval.py`): cách chia theo slot của 1.3.3 có
điểm mù — share tính theo SỐ vị thế đang mở, không theo số tiền đã dùng, nên một vị thế trước nhỏ
(lệnh BNB 833$ từ sizing cũ trước 1.3.3) hoặc bị hệ số SHORT BNB 0,5 vẫn tốn trọn slot của nó
(lệnh BTC vào sau chỉ được 30% = 7.500$ dù ngân sách mới dùng 833$). Nay vị thế thứ 1/2/3 lấy lần lượt
**50% / 60% / 100% ngân sách CÒN LẠI** (`gross_remain_fracs`; còn lại = ngân sách − tổng notional đang mở),
**trần mỗi lệnh 50% ngân sách** (`gross_cap_frac`, tính trước hệ số coin). Khi các vị thế trước đủ size,
công thức này cho ra đúng 50/30/20 như cũ; trường hợp lệnh BNB 833$ ở trên, lệnh kế tiếp được
60% × (25.000 − 833) = 14.500 → trần 12.500$. Nếu ngân sách còn lại = 0 (ví dụ van tiết lưu thu ngân sách
xuống dưới mức đã dùng), tín hiệu bị bỏ qua kèm sự kiện `TRENDMR_SKIP`. Backtest đối chứng ở x5 + van:
2025 +694% (DD −18,4%) so với slot +662% (−18,7%); 2026 +299% (DD −22,7%) so với slot +310% (−19,5%) —
gần trung tính, đổi vì dùng đúng ngân sách theo ý user. **Gross giữ x5**: đã đánh giá x3–x10 (biên→dải giữa
trung vị 1,07–1,63% ≈ stop 1,5%; volume lệnh không phải nút thắt ở quy mô này); x5 là điểm cân bằng —
lên x8/x10, DD trung vị Monte Carlo khi edge mòn một nửa đi từ −50% (x5) xuống −64% / −71%.

## Cổng quản trị hiệu suất — REVIEW_HOLD

Module tự đối chiếu sau **mỗi lệnh đóng** (đây là cổng quản trị do user đặt, khác các cảnh báo
TRENDMR_ALERT chỉ-báo-không-chặn): mốc theo số lệnh đã đóng — **30 lệnh: winrate ≥ 55%, PF ≥ 0,9**;
**75 lệnh: winrate ≥ 60%, PF ≥ 1,0 và PnL lũy kế > 0**; **150 lệnh: winrate ≥ 65%, PF ≥ 1,2** (trùng tiêu chí
nghiệm thu) — cộng thêm **PF của 50 lệnh gần nhất ≥ 1,0**. Vi phạm bất kỳ ngưỡng nào → sự kiện
`TRENDMR_REVIEW_HOLD`, module **dừng vào lệnh mới** (vị thế đang mở vẫn quản trị và thoát bình thường).
Phiên kiểm tra 1h (`paper-trendmr-health`, Phần D) thực hiện review: chẩn đoán theo coin/lý do thoát/chất
lượng khớp/regime, ghi `strategy_watch.md`, gửi user kèm đề xuất điều chỉnh + kế hoạch backtest đối chứng.
Chỉ gỡ hold sau khi user duyệt — người vận hành gọi `clear_review_hold()` trên module (hoặc sửa
`review_hold` trong state khi service dừng) rồi kiểm chứng lại trạng thái.

## Đường LIVE đang khóa có chủ đích

`allow_live = false`: khi chạy không-paper, module chỉ ghi sự kiện `TRENDMR_LIVE_LOCKED`, **không gửi lệnh**.
Chỉ mở live sau khi paper đạt tiêu chí nghiệm thu (4–6 tuần, ≥ 150 lệnh đóng, winrate ≥ 65%, PF ≥ 1,2)
và có quyết định riêng. ASTER live hiện tại không bị ảnh hưởng bởi bất kỳ công tắc nào của module này.
