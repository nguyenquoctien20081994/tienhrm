# VietTam Core Hub

> Tên cũ: **Thermal Core HUD** (`ThermalCoreHUD.exe`, đến bản 1.9.1). Lần đầu chạy `viettam-core-hub.exe`, app tự chuyển thư mục
> dữ liệu cũ `%LOCALAPPDATA%\ThermalCoreHUD` sang `%LOCALAPPDATA%\viettam-core-hub` – cài đặt, nhật ký nhiệt độ, ảnh nhân vật/logo
> và LibreHardwareMonitor đã tải được giữ nguyên. Nếu bản cũ đang chạy, app sẽ nhắc đóng bản cũ trước. Giao diện sci-fi vẫn mang
> tên "Thermal Core" (là tên kiểu giao diện, chọn trong Cài đặt).

App chạy local trên máy tính, đo **nhiệt độ CPU, GPU, Mainboard, ổ cứng** (mặc định 6 giây/lần, chỉnh được 1 giây hoặc 5–60 giây), có **thanh nhận định bằng lời** cho người không chuyên, giao diện đồng hồ cơ kết hợp HUD gaming/viễn tưởng.

Bản này được chỉnh cho **ThinkPad X1 Yoga Gen 5 · Intel Core i7-10610U · Intel UHD Graphics · Windows 11 Pro 25H2**, và vẫn chạy được trên các máy Windows/Linux khác.

---

## 1. Cài đặt và chạy (Windows)

### Cách 1 – File .exe (khuyên dùng, không cần Python)
1. Vào trang **Releases** của repo, tải `viettam-core-hub.exe` ở bản mới nhất.
2. Nhấp đúp `viettam-core-hub.exe` → bấm **Yes** ở hộp thoại quyền Administrator.
   - Nếu Windows hiện *"Windows protected your PC"* (vì file chưa có chữ ký số): bấm **More info → Run anyway**.
3. Lần đầu chạy, app tự tải **LibreHardwareMonitor v0.9.6** (khoảng 6 MB, có kiểm tra SHA256) vào `%LOCALAPPDATA%\viettam-core-hub\lhm`, bật web server nội bộ rồi khởi động nó.
   - Nếu hiện hỏi *"PawnIO is not installed, do you want to install it?"* → bấm **OK**. PawnIO là driver đọc cảm biến CPU, chỉ cần cài một lần.
4. Giao diện mở trong một cửa sổ riêng. **Đóng cửa sổ là app tự thoát**, kèm theo tắt LibreHardwareMonitor nếu chính app đã bật nó.
5. Mở lại khi app đang chạy thì app chỉ hiện lại cửa sổ, không chạy thêm bản thứ hai.

Nhật ký lỗi nằm ở `%LOCALAPPDATA%\viettam-core-hub\viettam-core-hub.log`.

### Cách 2 – Chạy từ mã nguồn Python
Cài Python 3.9+ (`winget install -e --id Python.Python.3.12`), rồi nhấp đúp `start-windows.bat`.

Muốn xem thử giao diện mà không cần cảm biến: `viettam-core-hub.exe --demo` hoặc `python app.py --demo`.

## 2. Trên máy anh, mỗi đồng hồ đo gì

| Đồng hồ | Nguồn dữ liệu trên X1 Yoga Gen 5 | Độ tin cậy |
|---|---|---|
| **CPU CORE** | `CPU Package` (cảm biến số DTS trong CPU, đọc qua LibreHardwareMonitor) | Đo thật, chính xác |
| **GPU CORE** | Intel UHD là GPU tích hợp, **nằm chung đế (die) với CPU** và không có cảm biến nhiệt riêng → hiển thị `iGPU ≈ CPU Package` | **Ước tính** (đã ghi rõ trên giao diện) |
| **MAINBOARD** | ThinkPad không có chip SuperIO như PC để bàn. App lần lượt thử: (1) vùng nhiệt ACPI của Embedded Controller (`ACPI THM0`, cần quyền Administrator); (2) nếu không có thì dùng nhiệt độ SSD NVMe gắn trên mainboard, hiển thị nhãn **THAY THẾ** | Gần đúng |

- Mỗi đồng hồ có menu chọn cảm biến ở góc phải (`AUTO` hoặc chọn cảm biến cụ thể). Lựa chọn được lưu lại.
- Khu **SENSOR ARRAY** bên dưới liệt kê toàn bộ cảm biến nhiệt độ đọc được.
- Các cảm biến `Distance to TjMax` bị loại vì đó là *khoảng cách tới ngưỡng*, không phải nhiệt độ.

## 3. Giao diện

### Thanh "Tình trạng nhiệt" (trên cùng)
Nhận định bằng lời dễ hiểu kèm **điểm nhiệt 0–100** (100 = rất mát, 60 = chạm ngưỡng cảnh báo, dưới 20 = nguy hiểm):

| Mức | Ví dụ thông điệp |
|---|---|
| ❄ Rất mát | "Máy đang rất mát mẻ – mọi thứ ổn định" / "…tản nhiệt hoạt động xuất sắc" |
| ✓ Bình thường | "Nhiệt độ bình thường – máy hoạt động ổn định" |
| ▲ Hơi ấm | "Máy hơi ấm – vẫn trong ngưỡng an toàn" |
| ⚠ Nóng | "Máy đang nóng – nên giảm tải hoặc cải thiện thông gió" |
| ⛔ Quá nhiệt | "Quá nhiệt! – hãy đóng bớt ứng dụng nặng ngay" |

App không chỉ so nhiệt độ với ngưỡng mà còn **xét mức tải CPU**:
- CPU chạy nặng mà vẫn mát → "tản nhiệt làm việc rất tốt".
- CPU đang nghỉ mà đã nóng → "tản nhiệt có dấu hiệu kém", kèm gợi ý kiểm tra khe gió, vệ sinh quạt, thay keo tản nhiệt.
- Nóng vì đang chạy nặng → giải thích đây là phản ứng bình thường.
- Nhiệt tăng nhanh (≥ 4°C/phút), CPU chạm TjMax (tự giảm xung) → cảnh báo riêng.

Kết luận "tản nhiệt tốt/kém" chỉ đưa ra khi có số liệu tải làm căn cứ.

### Hai kiểu đồng hồ, hai chế độ màu
- Nút **HUD / COCKPIT**: HUD là đồng hồ cơ tròn; COCKPIT là thanh đo dọc kiểu buồng lái máy bay, có con trỏ, dải vùng nhiệt và số đọc lớn.
- Nút **◐ AUTO / ☾ ĐÊM / ☀ NGÀY**: AUTO tự đổi theo giờ (6:00–18:00 là ngày). Mặt đồng hồ luôn giữ nền tối như đồng hồ thật.

### Giao diện Việt Tâm (nổi khối 3D, tối giản)
Thiết kế theo mẫu trang chủ nội bộ của công ty, sắp xếp theo thứ tự người xem cần:

1. **Thẻ trạng thái** – trả lời "máy có ổn không?" trong 1 cái nhìn: 1 tiêu đề (vd. *Máy hoạt động bình thường*),
   1 dòng số liệu (*Cao nhất: CPU 69°C, còn 16°C tới ngưỡng cảnh báo*), 1 lời khuyên, điểm nhiệt, và 2 nhân viên Việt Tâm.
2. **3 đồng hồ** – CPU, Card đồ hoạ, Mainboard: số đo lớn, vòng đo **xanh = ổn định**, **cam = cao**, **đỏ = quá nhiệt**;
   bên dưới là Thấp nhất / Trung bình / Cao nhất và tên phần cứng.
3. **Tab Chi tiết** (nhiệt từng nhân CPU, sức khoẻ ổ cứng, tất cả cảm biến) và **tab Nhật ký** (biểu đồ 1 giờ – 30 ngày)
   – chỉ mở khi cần, không chiếm màn hình chính.

Thanh trên chỉ có logo (kèm chấm trạng thái: xanh = đang đo, cam = dữ liệu mô phỏng, đỏ = mất tín hiệu), 3 tab và 3 nút:
**đồng hồ nổi góc màn hình**, **công tắc ngày/đêm**, **cài đặt**. Các tuỳ chọn ít dùng nằm trong **Cài đặt**: giao diện
(Việt Tâm / Thermal Core), chế độ màu (tự động/ngày/đêm), °C/°F, còi báo quá nhiệt, chu kỳ đo, nguồn mainboard A/B,
cảm biến cho từng đồng hồ, ngưỡng cảnh báo, ảnh nhân vật & logo. Trang Tổng quan vừa trọn 1 màn hình từ 1280×720 trở lên.

- Nhân vật đổi tư thế theo mức nhiệt (ảnh trong `web/img/vt/<nam|nu>-<mức>.webp`):

  | Mức nhiệt | Nam | Nữ |
  |---|---|---|
  | Đang chờ số liệu | Khoanh tay | Ôm sổ |
  | Mát | Giơ ngón cái | Tạo dáng chữ V, nháy mắt |
  | Bình thường | Vẫy tay | Vẫy tay |
  | Hơi ấm | Cầm laptop | Cầm laptop |
  | Nóng | Gọi điện | Chỉ tay, cầm sổ |
  | Quá nhiệt | Chỉ tay khẩn cấp | Gọi điện báo IT/HCNS |

  Khi có lời khuyên cụ thể (vd. ổ cứng cần sao lưu), thẻ trạng thái hiện đúng lời khuyên đó.
- Chấm màu cạnh tên đồng hồ giữ màu nhận diện (CPU xanh ngọc, GPU hồng, Mainboard lục) giống biểu đồ nhật ký.
- **Thay ảnh khác** (không bắt buộc): **Cài đặt** → *Ảnh nhân vật & logo* → **CHỌN ẢNH** cho *Nhân vật nam*, *Nhân vật nữ*,
  *Logo công ty*. Ảnh thay thế dùng chung cho mọi mức nhiệt, được thu nhỏ còn tối đa 640 px và lưu trong
  `%LOCALAPPDATA%\viettam-core-hub\brand\` (không gửi ra ngoài). Chỉ nhận PNG/JPG/GIF/WebP (không nhận SVG vì lý do bảo mật).
  Bấm **XOÁ** để quay về ảnh có sẵn.
- Ảnh nhân vật và logo là tài sản của Công ty TNHH Thiết Bị Việt Tâm, chỉ dùng cho app nội bộ này.

### CORE MAP – bản đồ nhiệt từng nhân CPU
Mỗi hàng là một nhân, mỗi cột là một lần đo trong 60 phút gần nhất; màu càng đậm/càng cam là càng nóng (thang cố định 30–100°C). Cột bên phải ghi nhiệt độ và tải hiện tại của từng nhân. Rê chuột để xem chi tiết. Nhân nào nóng hơn hẳn các nhân khác có thể do lỗi keo tản nhiệt.

### Sức khoẻ ổ cứng (S.M.A.R.T)
Mỗi ổ có: % tuổi thọ còn lại (✓ TỐT / ▲ THEO DÕI dưới 30% / ⛔ NÊN THAY dưới 10%), nhiệt độ so với ngưỡng của nhà sản xuất, tổng dữ liệu đã ghi/đọc, giờ hoạt động, số lần bật, dung lượng đã dùng, vùng dự phòng (NVMe). Ổ gần hết tuổi thọ hoặc hết vùng dự phòng → thanh nhận định nhắc sao lưu. Ổ SATA đời cũ có thể chỉ báo nhiệt độ.

### Nhật ký nhiệt độ (lưu 7/14/30 ngày)
- Mỗi lần đo được ghi vào `%LOCALAPPDATA%\viettam-core-hub\history.db` (SQLite, khoảng 0,3 MB/ngày). Đổi số ngày lưu trong **CALIBRATE**.
- Tab **1 GIỜ / 24 GIỜ / 7 NGÀY / 30 NGÀY**: biểu đồ đường CPU/GPU/BOARD (khoảng dài có thêm vùng nhạt = mức cao nhất của CPU), đường đứt nét = ngưỡng cảnh báo.
- Tóm tắt bằng lời, ví dụ *"Máy thường nóng nhất khoảng 13:00–14:00 (CPU trung bình 71°C, tải 61%)"*.
- Các ô số: CPU trung bình, cao nhất (kèm thời điểm), số phút vượt ngưỡng, số phút hạ xung do nhiệt.
- Biểu đồ **CPU trung bình theo giờ trong ngày** và **bản đồ nhiệt ngày × giờ**.

### Phát hiện CPU tự hạ xung (throttling)
LibreHardwareMonitor không báo trực tiếp trạng thái hạ xung, nên app **suy luận** bằng cách so xung của nhân nhanh nhất với mức cao nhất CPU từng đạt khi tải nặng (lưu trong nhật ký):
- Tải ≥ 50%, xung < 80% mức cao nhất và CPU đang rất nóng (gần TjMax) → **⚠ HẠ XUNG DO NHIỆT**: máy chậm đi vì nóng.
- Tải ≥ 80%, xung thấp nhưng CPU không nóng → **GIỚI HẠN XUNG**: do chế độ tiết kiệm pin/điện năng hoặc giới hạn công suất, không phải do nhiệt.
- Cần vài phút chạy nặng đầu tiên để app biết "mức xung cao nhất" của máy.

### Thông số phụ dưới mỗi đồng hồ
- **CPU:** tải (%), công suất (W), xung nhịp (GHz), khoảng cách tới **TjMax** (nhiệt độ CPU tự giảm xung để bảo vệ).
- **GPU:** tải (%); Intel iGPU ghi rõ "chung đế CPU".
- **Mainboard/ổ cứng:** ngưỡng cảnh báo do chính nhà sản xuất ổ công bố; tốc độ quạt (RPM) nếu máy có cảm biến quạt.
- Thanh **NEXT SCAN** đếm ngược tới lần đo kế tiếp; biểu đồ lưu 60 phút gần nhất; ô **Δ/PHÚT** cho biết nhiệt đang tăng hay giảm.

- **Đồng hồ cơ**: vành thép có khía, ốc vít, mặt số vân guilloche, trục đồng gắn chân kính ruby, kim có quán tính lò xo (hơi vọt rồi dừng như đồng hồ thật).
- **Bánh răng** sau mặt số quay nhanh dần khi nhiệt độ tăng.
- **Mặt số phụ Δ/10s**: tốc độ tăng/giảm nhiệt trong 10 giây gần nhất.
- Vạch tam giác **xanh/đỏ** trên vành: nhiệt độ thấp nhất/cao nhất trong phiên.
- Biểu đồ 60 phút gần nhất, các ô MIN/AVG/MAX.
- **Cảnh báo**: vượt WARN thì thẻ chuyển màu cam; vượt CRIT thì báo động đỏ nhấp nháy, có băng cảnh báo và còi (nút `ALARM`, mặc định tắt).
- Nút `°C/°F`, `CALIBRATE` (chỉnh ngưỡng), `⛶` (toàn màn hình).
- **Co giãn theo cửa sổ**: 3 đồng hồ luôn nằm trên một hàng ngang. Khi thu hẹp cửa sổ, bên trong mỗi thẻ tự gọn lại theo bề rộng của thẻ: menu chọn cảm biến xuống dòng, ô số liệu chuyển thành 2 cột, rồi ẩn bớt biểu đồ.

### Overlay – 3 đồng hồ nổi ở góc trên bên phải màn hình

Bấm nút **OVERLAY** trên thanh công cụ (hoặc chạy `viettam-core-hub.exe --overlay` để chỉ mở overlay). Overlay là cửa sổ không viền, trong suốt mờ (mặc định đậm 55%, cuộn chuột để chỉnh) và **luôn nằm trên mọi app khác**:

| Thao tác | Kết quả |
|---|---|
| Kéo chuột trái | Di chuyển overlay (vị trí được nhớ cho lần sau) |
| Cuộn chuột | Tăng/giảm độ trong suốt (25–100 %) |
| Nhấp đúp | Mở cửa sổ chính |
| Chuột phải | Menu: mở cửa sổ chính, chọn độ trong suốt, về góc trên bên phải, ẩn overlay, thoát app |
| Nút × | Ẩn overlay |

- Overlay dùng chung ngưỡng cảnh báo, đơn vị °C/°F và lựa chọn cảm biến với cửa sổ chính. Nhãn có dấu `*` (ví dụ `BOARD*`) nghĩa là đang dùng cảm biến thay thế.
- Có thể đóng cửa sổ chính mà vẫn giữ overlay. App chỉ tự thoát khi **cả** cửa sổ chính và overlay đều đã đóng; lần mở sau, overlay tự bật lại nếu lần trước đang bật.
- Kim chỉ chuyển động mượt (30 khung hình/giây) khi nhiệt độ thay đổi; khi nhiệt độ ổn định, overlay chỉ vẽ lại 2 lần/giây nên gần như không tốn CPU.
- Giới hạn: overlay không đè lên được game chạy chế độ *exclusive fullscreen*. Hãy chọn *borderless/windowed fullscreen* trong game.

Ngưỡng mặc định:

| | WARN | CRIT | Lý do |
|---|---|---|---|
| CPU | 85 °C | 95 °C | i7-10610U có TjMax 100 °C; laptop mỏng chạy tải nặng thường ở mức 85–95 °C |
| GPU (iGPU) | 85 °C | 95 °C | Chung đế với CPU |
| Mainboard | 65 °C | 80 °C | EC/SSD; SSD NVMe thường bắt đầu giảm tốc (throttle) quanh 70–80 °C |

### Nguồn nhiệt độ mainboard: A hoặc B
Trên thẻ **MAINBOARD** có nút **NGUỒN A · LHM/SSD / B · HWiNFO** (cũng chỉnh được trong **CALIBRATE**):

| | A – LHM / ổ SSD (mặc định) | B – HWiNFO |
|---|---|---|
| Lấy số từ | LibreHardwareMonitor; máy không có cảm biến mainboard thì dùng nhiệt độ ổ SSD (nhãn THAY THẾ) | Phần mềm HWiNFO – nhận diện được nhiều chip mainboard mà LHM chưa hỗ trợ (vd. Lenovo ThinkCentre M910q) |
| Cần cài thêm | Không | HWiNFO (miễn phí) |
| Hạn chế | Không có nhiệt độ mainboard thật trên một số máy | Bản miễn phí **tự tắt chia sẻ sau 12 giờ**, phải bật lại; bản Pro không giới hạn |

Cài đặt cho nguồn B:
1. Tải HWiNFO từ trang chính thức `hwinfo.com`, cài và mở bằng chế độ **Sensors-only**.
2. Mở **Settings chính** của HWiNFO (không phải nút bánh răng trong cửa sổ cảm biến – nút đó là *Sensor Settings*):
   - lúc khởi động: bấm nút **Settings** trên hộp thoại đầu tiên; hoặc
   - khi HWiNFO đang chạy: **chuột phải biểu tượng HWiNFO ở khay hệ thống** (góc dưới phải, có thể nằm trong mũi tên ^) → **Settings**.

   Ở tab đầu tiên (**General / User Interface**) đánh dấu **Shared Memory Support** → OK. Có thể bật thêm *Auto Start* để HWiNFO chạy cùng Windows.
3. Trong Thermal Core, bấm **B · HWiNFO**. Bên cạnh nút hiện **✓ đang dùng** nếu đọc được.
4. Nếu hiện **⚠ lỗi → tạm dùng A** (rê chuột để xem lý do): HWiNFO chưa chạy, chưa bật chia sẻ, hoặc đã hết 12 giờ – bật lại Shared Memory Support trong HWiNFO. Trong lúc đó đồng hồ MAINBOARD tự dùng nguồn A, không bị trống.
5. Ô chọn cảm biến của thẻ MAINBOARD phải để **AUTO** thì nút A/B mới có tác dụng. Nếu đang chọn tay một cảm biến
   (vd. *Composite Temperature — WDC PC SN730*), bên cạnh nút hiện **⚠ đang chọn tay → chọn AUTO**. Từ bản 1.5.1, bấm đổi A/B
   sẽ tự đưa ô này về AUTO.
6. Với Lenovo ThinkCentre (chip **Nuvoton NCT6685D**), nguồn B lấy **PCH Core** làm nhiệt độ mainboard. Các kênh HWiNFO
   không có tên (thường là chân cảm biến bỏ trống, số liệu vô nghĩa như 76/87/66 °C) chỉ hiện trong SENSOR ARRAY với tên
   *Kênh không tên #n*, không dùng để đánh giá nhiệt độ.

## 4. Tham số dòng lệnh

```
viettam-core-hub.exe (hoặc python app.py)
    [--port 8787] [--interval 1.0] [--demo]
    [--lhm-url http://127.0.0.1:8085/data.json] [--lhm-auth user:pass] [--no-manage-lhm]
    [--ui auto|window|browser|none] [--overlay] [--keep-running] [--version]
```

- `--interval`: chu kỳ đo cho phiên này (mặc định lấy theo cài đặt, 6 giây; đổi lâu dài trong **Cài đặt** / **CALIBRATE**).
- **Đo 1 giây/lần**: màn hình và đồng hồ nổi cập nhật mỗi giây; nhật ký vẫn chỉ ghi 5 giây/lần để file nhật ký không phình to
  và đỡ ghi ổ đĩa. LibreHardwareMonitor mặc định làm mới 1 giây/lần nên số liệu theo kịp; nếu dùng nguồn mainboard B, HWiNFO mặc
  định chỉ làm mới 2 giây/lần – muốn 1 giây thì vào HWiNFO *Sensor Settings → Polling Period* đặt 1000 ms. Đo 1 giây tốn CPU hơn
  một chút (app đọc và phân tích dữ liệu cảm biến mỗi giây); máy yếu nên dùng 5–6 giây.
- `--overlay`: chỉ hiện overlay 3 đồng hồ, không mở cửa sổ chính. Có thể tạo shortcut với tham số này và đặt vào thư mục Startup (`Win + R` → `shell:startup`) để overlay tự bật khi khởi động máy.

- `--no-manage-lhm`: không tự tải/bật LibreHardwareMonitor (khi anh tự quản lý LHM).
- `--keep-running`: không tự thoát khi đóng cửa sổ.
- Tạo shortcut có tham số: chuột phải exe → Create shortcut → sửa ô *Target*, ví dụ `...\viettam-core-hub.exe --interval 0.5`.

## 5. Kiến trúc

```
viettam-core-hub.exe (UAC admin) ─ tự tải, cấu hình, khởi động ─┐
                                                              ▼
LibreHardwareMonitor (admin, PawnIO)      ACPI/WMI (admin)
      │  http://127.0.0.1:8085/data.json         │ PowerShell, 5 giây/lần
      ▼                                          ▼
 app.py ── Sampler (luồng nền, 6 giây/lần) ── sensors.py + assess.py (nhận định)
      │  Server-Sent Events /api/stream  (chỉ lắng nghe 127.0.0.1)
      ▼
 web/  (HTML + Canvas, không dùng thư viện ngoài, chạy được khi không có mạng)
```

- Backend chỉ dùng thư viện chuẩn của Python; overlay cần thêm Pillow (`requirements.txt`).
- Ngưỡng cảnh báo, đơn vị, lựa chọn cảm biến và vị trí/độ trong suốt của overlay được lưu ở `%LOCALAPPDATA%\viettam-core-hub\settings.json`.
- Cả app lẫn web server của LibreHardwareMonitor chỉ nghe trên `127.0.0.1`, máy khác trong mạng LAN không truy cập được.
- Trên Linux, app đọc `/sys/class/hwmon` và dùng thêm `nvidia-smi` nếu có.
- App **không bao giờ** tự chuyển sang số liệu giả khi mất cảm biến. Chế độ DEMO chỉ chạy khi gọi `--demo`, hoặc khi mở trực tiếp file HTML mà không có backend, và luôn có nhãn DEMO rõ ràng.

## 6. Xử lý sự cố

> **Thiếu nhiệt độ mainboard?** Mở **CALIBRATE → XUẤT FILE CHẨN ĐOÁN** và gửi file `viettam-core-hub-chan-doan-….json` cho người hỗ trợ: file cho biết LibreHardwareMonitor có nhận ra chip giám sát mainboard (SuperIO, ví dụ Nuvoton NCT6683D) hay không. Nếu chip có mặt nhưng chỉ trả về 0°C / ≥125°C (kênh không dùng), app tự bỏ qua và dùng nguồn thay thế.
>
> **Lenovo ThinkCentre M910q (và nhiều máy mini/văn phòng):** LibreHardwareMonitor không đọc được cảm biến mainboard của dòng này (không hỗ trợ chip giám sát của Lenovo, không có nhiệt độ PCH). App tự dùng nhiệt độ ổ SSD (nằm trên mainboard) và ghi nhãn **THAY THẾ**. Đây là giới hạn phần cứng/driver, không phải lỗi app.
>
> Bản ≤ 1.2 hiển thị ổ cứng **87°C đứng yên**: đó là *ngưỡng cảnh báo* (Warning/Critical Temperature) của ổ bị hiểu nhầm là nhiệt độ. Bản 1.3 đã tách ra và dùng nó làm ngưỡng cảnh báo riêng cho từng ổ.

| Triệu chứng | Nguyên nhân | Cách xử lý |
|---|---|---|
| Băng vàng: *"Không kết nối được LibreHardwareMonitor"* | LHM chưa chạy, hoặc web server chưa bật | Thoát app rồi mở lại (app tự sửa cấu hình). Hoặc mở LHM → Options → Remote Web Server → Run |
| Băng vàng: *"Không chuẩn bị được LibreHardwareMonitor"* | Không có mạng ở lần chạy đầu, hoặc bị chặn tải | Kết nối mạng rồi mở lại. Hoặc tải `LibreHardwareMonitor.zip` v0.9.6, giải nén vào thư mục `lhm\` đặt cạnh exe |
| CPU báo `NO SENSOR` | Chưa cài PawnIO | Thoát app, mở lại, đồng ý cài PawnIO khi được hỏi |
| Windows Defender báo `viettam-core-hub.exe` là virus | Báo nhầm, thường gặp với exe đóng gói bằng PyInstaller | Đối chiếu mã SHA256 ở trang Release (`Get-FileHash viettam-core-hub.exe`); nếu khớp thì Allow trong Protection history. Hoặc dùng Cách 2 (chạy từ mã nguồn) |
| Windows chặn exe hoặc LHM | Smart App Control / SmartScreen | Bấm More info → Run anyway; hoặc chuột phải → Properties → Unblock |
| Mainboard luôn hiện **THAY THẾ** (SSD) | Firmware không cho đọc ACPI thermal zone qua WMI | Bình thường với nhiều laptop; có thể chọn cảm biến khác ở menu |
| Nút OVERLAY bị mờ | Bản chạy từ mã nguồn thiếu Pillow | `py -3 -m pip install -r requirements.txt` (bản .exe đã có sẵn) |
| Cổng 8787 bị chiếm | Có ứng dụng khác dùng cổng này | `start-windows.bat --port 8899` |

## 7. Build file .exe

- **Tự động**: mỗi lần push thay đổi trong `thermal-monitor/`, GitHub Actions (`.github/workflows/viettam-core-hub.yml`) build trên Windows, chạy unit test, chạy thử exe (chế độ demo và chế độ thật có tự tải LHM), rồi đăng `viettam-core-hub.exe` cùng mã SHA256 lên trang Releases.
- **Thủ công** trên máy Windows có Python: chạy `build-exe.bat` → `dist\viettam-core-hub.exe`.

## 8. Tác giả và giấy phép bên thứ ba

**VietTam Core Hub** – thiết kế & phát triển: **Nguyễn Quốc Tiến** (`nguyenquoctien`). Chữ ký tác giả có ở chân trang,
chìm trong nền giao diện, trong thông tin file (`viettam-core-hub.exe` → Properties → Details) và trong `/api/info`.

Toàn bộ mã nguồn trong thư mục `thermal-monitor/` được viết riêng cho dự án này. App dùng các thành phần mã nguồn mở / miễn phí sau
(không sửa đổi mã của chúng):

| Thành phần | Dùng để | Giấy phép | Cách đi kèm |
|---|---|---|---|
| [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) v0.9.6 | Đọc cảm biến trên Windows | MPL-2.0 | Không đóng gói; app tự tải bản chính thức từ GitHub (kiểm tra SHA256) ở lần chạy đầu |
| PawnIO | Driver cho LHM đọc cảm biến | của tác giả PawnIO | Do LibreHardwareMonitor đề nghị cài; app không đóng gói |
| HWiNFO (tuỳ chọn, nguồn B) | Nhiệt độ mainboard | Freeware (không mã nguồn mở) | Người dùng tự cài; app chỉ đọc bộ nhớ chia sẻ |
| Python, Tcl/Tk | Ngôn ngữ chạy app, cửa sổ overlay | PSF License, BSD-style | Đóng gói trong exe |
| Pillow | Vẽ overlay | MIT-CMU | Đóng gói trong exe |
| Oswald, Nunito, Dancing Script, Great Vibes | Font giao diện Việt Tâm (`web/fonts/`) | SIL Open Font License 1.1 | Đóng gói trong exe |
| PyInstaller | Đóng gói exe | GPL-2.0 kèm ngoại lệ bootloader (cho phép phát hành exe theo giấy phép tuỳ ý) | Chỉ dùng lúc build |

## 9. Kiểm thử

```
python -m unittest discover -s tests -v
```

Bộ test dùng dữ liệu `data.json` mẫu: ThinkPad X1 Yoga Gen 5, laptop có card rời NVIDIA, PC để bàn có SuperIO (định dạng LHM cũ, giá trị °F), cùng Linux hwmon và `nvidia-smi`.
