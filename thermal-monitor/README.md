# Thermal Core HUD

App chạy local trên máy tính, đo **nhiệt độ CPU, GPU, Mainboard, ổ cứng** (mặc định 15 giây/lần, chỉnh được 5–60 giây), có **thanh nhận định bằng lời** cho người không chuyên, giao diện đồng hồ cơ kết hợp HUD gaming/viễn tưởng.

Bản này được chỉnh cho **ThinkPad X1 Yoga Gen 5 · Intel Core i7-10610U · Intel UHD Graphics · Windows 11 Pro 25H2**, và vẫn chạy được trên các máy Windows/Linux khác.

---

## 1. Cài đặt và chạy (Windows)

### Cách 1 – File .exe (khuyên dùng, không cần Python)
1. Vào trang **Releases** của repo, tải `ThermalCoreHUD.exe` ở bản mới nhất.
2. Nhấp đúp `ThermalCoreHUD.exe` → bấm **Yes** ở hộp thoại quyền Administrator.
   - Nếu Windows hiện *"Windows protected your PC"* (vì file chưa có chữ ký số): bấm **More info → Run anyway**.
3. Lần đầu chạy, app tự tải **LibreHardwareMonitor v0.9.6** (khoảng 6 MB, có kiểm tra SHA256) vào `%LOCALAPPDATA%\ThermalCoreHUD\lhm`, bật web server nội bộ rồi khởi động nó.
   - Nếu hiện hỏi *"PawnIO is not installed, do you want to install it?"* → bấm **OK**. PawnIO là driver đọc cảm biến CPU, chỉ cần cài một lần.
4. Giao diện mở trong một cửa sổ riêng. **Đóng cửa sổ là app tự thoát**, kèm theo tắt LibreHardwareMonitor nếu chính app đã bật nó.
5. Mở lại khi app đang chạy thì app chỉ hiện lại cửa sổ, không chạy thêm bản thứ hai.

Nhật ký lỗi nằm ở `%LOCALAPPDATA%\ThermalCoreHUD\thermal-core.log`.

### Cách 2 – Chạy từ mã nguồn Python
Cài Python 3.9+ (`winget install -e --id Python.Python.3.12`), rồi nhấp đúp `start-windows.bat`.

Muốn xem thử giao diện mà không cần cảm biến: `ThermalCoreHUD.exe --demo` hoặc `python app.py --demo`.

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

Bấm nút **OVERLAY** trên thanh công cụ (hoặc chạy `ThermalCoreHUD.exe --overlay` để chỉ mở overlay). Overlay là cửa sổ không viền, trong suốt mờ và **luôn nằm trên mọi app khác**:

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

## 4. Tham số dòng lệnh

```
ThermalCoreHUD.exe (hoặc python app.py)
    [--port 8787] [--interval 1.0] [--demo]
    [--lhm-url http://127.0.0.1:8085/data.json] [--lhm-auth user:pass] [--no-manage-lhm]
    [--ui auto|window|browser|none] [--overlay] [--keep-running] [--version]
```

- `--interval`: chu kỳ đo cho phiên này (mặc định lấy theo cài đặt, 15 giây; đổi lâu dài trong **CALIBRATE**).
- `--overlay`: chỉ hiện overlay 3 đồng hồ, không mở cửa sổ chính. Có thể tạo shortcut với tham số này và đặt vào thư mục Startup (`Win + R` → `shell:startup`) để overlay tự bật khi khởi động máy.

- `--no-manage-lhm`: không tự tải/bật LibreHardwareMonitor (khi anh tự quản lý LHM).
- `--keep-running`: không tự thoát khi đóng cửa sổ.
- Tạo shortcut có tham số: chuột phải exe → Create shortcut → sửa ô *Target*, ví dụ `...\ThermalCoreHUD.exe --interval 0.5`.

## 5. Kiến trúc

```
ThermalCoreHUD.exe (UAC admin) ─ tự tải, cấu hình, khởi động ─┐
                                                              ▼
LibreHardwareMonitor (admin, PawnIO)      ACPI/WMI (admin)
      │  http://127.0.0.1:8085/data.json         │ PowerShell, 5 giây/lần
      ▼                                          ▼
 app.py ── Sampler (luồng nền, 15 giây/lần) ── sensors.py + assess.py (nhận định)
      │  Server-Sent Events /api/stream  (chỉ lắng nghe 127.0.0.1)
      ▼
 web/  (HTML + Canvas, không dùng thư viện ngoài, chạy được khi không có mạng)
```

- Backend chỉ dùng thư viện chuẩn của Python; overlay cần thêm Pillow (`requirements.txt`).
- Ngưỡng cảnh báo, đơn vị, lựa chọn cảm biến và vị trí/độ trong suốt của overlay được lưu ở `%LOCALAPPDATA%\ThermalCoreHUD\settings.json`.
- Cả app lẫn web server của LibreHardwareMonitor chỉ nghe trên `127.0.0.1`, máy khác trong mạng LAN không truy cập được.
- Trên Linux, app đọc `/sys/class/hwmon` và dùng thêm `nvidia-smi` nếu có.
- App **không bao giờ** tự chuyển sang số liệu giả khi mất cảm biến. Chế độ DEMO chỉ chạy khi gọi `--demo`, hoặc khi mở trực tiếp file HTML mà không có backend, và luôn có nhãn DEMO rõ ràng.

## 6. Xử lý sự cố

> **Lenovo ThinkCentre M910q (và nhiều máy mini/văn phòng):** LibreHardwareMonitor không đọc được cảm biến mainboard của dòng này (không hỗ trợ chip giám sát của Lenovo, không có nhiệt độ PCH). App tự dùng nhiệt độ ổ SSD (nằm trên mainboard) và ghi nhãn **THAY THẾ**. Đây là giới hạn phần cứng/driver, không phải lỗi app.
>
> Bản ≤ 1.2 hiển thị ổ cứng **87°C đứng yên**: đó là *ngưỡng cảnh báo* (Warning/Critical Temperature) của ổ bị hiểu nhầm là nhiệt độ. Bản 1.3 đã tách ra và dùng nó làm ngưỡng cảnh báo riêng cho từng ổ.

| Triệu chứng | Nguyên nhân | Cách xử lý |
|---|---|---|
| Băng vàng: *"Không kết nối được LibreHardwareMonitor"* | LHM chưa chạy, hoặc web server chưa bật | Thoát app rồi mở lại (app tự sửa cấu hình). Hoặc mở LHM → Options → Remote Web Server → Run |
| Băng vàng: *"Không chuẩn bị được LibreHardwareMonitor"* | Không có mạng ở lần chạy đầu, hoặc bị chặn tải | Kết nối mạng rồi mở lại. Hoặc tải `LibreHardwareMonitor.zip` v0.9.6, giải nén vào thư mục `lhm\` đặt cạnh exe |
| CPU báo `NO SENSOR` | Chưa cài PawnIO | Thoát app, mở lại, đồng ý cài PawnIO khi được hỏi |
| Windows Defender báo `ThermalCoreHUD.exe` là virus | Báo nhầm, thường gặp với exe đóng gói bằng PyInstaller | Đối chiếu mã SHA256 ở trang Release (`Get-FileHash ThermalCoreHUD.exe`); nếu khớp thì Allow trong Protection history. Hoặc dùng Cách 2 (chạy từ mã nguồn) |
| Windows chặn exe hoặc LHM | Smart App Control / SmartScreen | Bấm More info → Run anyway; hoặc chuột phải → Properties → Unblock |
| Mainboard luôn hiện **THAY THẾ** (SSD) | Firmware không cho đọc ACPI thermal zone qua WMI | Bình thường với nhiều laptop; có thể chọn cảm biến khác ở menu |
| Nút OVERLAY bị mờ | Bản chạy từ mã nguồn thiếu Pillow | `py -3 -m pip install -r requirements.txt` (bản .exe đã có sẵn) |
| Cổng 8787 bị chiếm | Có ứng dụng khác dùng cổng này | `start-windows.bat --port 8899` |

## 7. Build file .exe

- **Tự động**: mỗi lần push thay đổi trong `thermal-monitor/`, GitHub Actions (`.github/workflows/thermal-core-hud.yml`) build trên Windows, chạy unit test, chạy thử exe (chế độ demo và chế độ thật có tự tải LHM), rồi đăng `ThermalCoreHUD.exe` cùng mã SHA256 lên trang Releases.
- **Thủ công** trên máy Windows có Python: chạy `build-exe.bat` → `dist\ThermalCoreHUD.exe`.

## 8. Kiểm thử

```
python -m unittest discover -s tests -v
```

Bộ test dùng dữ liệu `data.json` mẫu: ThinkPad X1 Yoga Gen 5, laptop có card rời NVIDIA, PC để bàn có SuperIO (định dạng LHM cũ, giá trị °F), cùng Linux hwmon và `nvidia-smi`.
