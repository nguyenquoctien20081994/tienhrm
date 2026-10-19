# Thermal Core HUD

App chạy local trên laptop, đo **nhiệt độ CPU, GPU, Mainboard** theo thời gian thực (mặc định 1 giây/lần), giao diện đồng hồ cơ kết hợp HUD gaming/viễn tưởng.

Bản này được chỉnh cho **ThinkPad X1 Yoga Gen 5 · Intel Core i7-10610U · Intel UHD Graphics · Windows 11 Pro 25H2**, và vẫn chạy được trên các máy Windows/Linux khác.

---

## 1. Cài đặt và chạy (Windows)

1. Cài **Python 3.9+** nếu máy chưa có. Script cài đặt sẽ tự cài Python 3.12 bằng `winget` nếu không tìm thấy.
2. Tải thư mục `thermal-monitor` về máy, ví dụ `D:\ThermalCore`.
3. Nhấp đúp **`start-windows.bat`** → bấm **Yes** ở hộp thoại UAC (chỉ hỏi 1 lần mỗi lần chạy).
   - Lần chạy đầu tiên, script tự tải **LibreHardwareMonitor v0.9.6** (có kiểm tra mã SHA256), rồi bật web server nội bộ `127.0.0.1:8085`.
   - Nếu LibreHardwareMonitor hỏi *"PawnIO is not installed, do you want to install it?"* → bấm **OK**. PawnIO là driver đọc cảm biến CPU; thiếu nó thì không có nhiệt độ CPU.
4. Giao diện mở trong một cửa sổ riêng (Edge chế độ app). Nếu không mở được, vào trình duyệt gõ `http://127.0.0.1:8787`.

Muốn xem thử giao diện mà không cần cảm biến: `python app.py --demo`.

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

- **Đồng hồ cơ**: vành thép có khía, ốc vít, mặt số vân guilloche, trục đồng gắn chân kính ruby, kim có quán tính lò xo (hơi vọt rồi dừng như đồng hồ thật).
- **Bánh răng** sau mặt số quay nhanh dần khi nhiệt độ tăng.
- **Mặt số phụ Δ/10s**: tốc độ tăng/giảm nhiệt trong 10 giây gần nhất.
- Vạch tam giác **xanh/đỏ** trên vành: nhiệt độ thấp nhất/cao nhất trong phiên.
- Biểu đồ 180 giây, các ô MIN/AVG/MAX.
- **Cảnh báo**: vượt WARN thì thẻ chuyển màu cam; vượt CRIT thì báo động đỏ nhấp nháy, có băng cảnh báo và còi (nút `ALARM`, mặc định tắt).
- Nút `°C/°F`, `CALIBRATE` (chỉnh ngưỡng), `⛶` (toàn màn hình).

Ngưỡng mặc định:

| | WARN | CRIT | Lý do |
|---|---|---|---|
| CPU | 85 °C | 95 °C | i7-10610U có TjMax 100 °C; laptop mỏng chạy tải nặng thường ở mức 85–95 °C |
| GPU (iGPU) | 85 °C | 95 °C | Chung đế với CPU |
| Mainboard | 65 °C | 80 °C | EC/SSD; SSD NVMe thường bắt đầu giảm tốc (throttle) quanh 70–80 °C |

## 4. Tham số dòng lệnh

```
python app.py [--port 8787] [--interval 1.0] [--demo]
              [--lhm-url http://localhost:8085/data.json] [--lhm-auth user:pass]
              [--ui auto|window|browser|none]
```

`start-windows.bat` chuyển tiếp mọi tham số, ví dụ: `start-windows.bat --interval 0.5`.

## 5. Kiến trúc

```
LibreHardwareMonitor (admin, PawnIO)      ACPI/WMI (admin)
      │  http://127.0.0.1:8085/data.json         │ PowerShell, 5 giây/lần
      ▼                                          ▼
 app.py ── Sampler (luồng nền, 1 giây/lần) ── sensors.py (phân loại, chọn cảm biến chính, ước tính iGPU)
      │  Server-Sent Events /api/stream  (chỉ lắng nghe 127.0.0.1)
      ▼
 web/  (HTML + Canvas, không dùng thư viện ngoài, chạy được khi không có mạng)
```

- Backend chỉ dùng thư viện chuẩn của Python, không cần `pip install`.
- Cả app lẫn web server của LibreHardwareMonitor chỉ nghe trên `127.0.0.1`, máy khác trong mạng LAN không truy cập được.
- Trên Linux, app đọc `/sys/class/hwmon` và dùng thêm `nvidia-smi` nếu có.
- App **không bao giờ** tự chuyển sang số liệu giả khi mất cảm biến. Chế độ DEMO chỉ chạy khi gọi `--demo`, hoặc khi mở trực tiếp file HTML mà không có backend, và luôn có nhãn DEMO rõ ràng.

## 6. Xử lý sự cố

| Triệu chứng | Nguyên nhân | Cách xử lý |
|---|---|---|
| Băng vàng: *"Không kết nối được LibreHardwareMonitor"* | LHM chưa chạy, hoặc web server chưa bật | Chạy lại `start-windows.bat`. Hoặc mở LHM → Options → Remote Web Server → Run |
| CPU báo `NO SENSOR` | Chưa cài PawnIO, hoặc LHM không chạy quyền Administrator | Mở `lhm\LibreHardwareMonitor.exe` bằng Run as administrator, đồng ý cài PawnIO |
| Windows chặn `LibreHardwareMonitor.exe` | Smart App Control / SmartScreen | Settings → Privacy & security → Windows Security → App & browser control; hoặc chuột phải → Properties → Unblock |
| Mainboard luôn hiện **THAY THẾ** (SSD) | Firmware không cho đọc ACPI thermal zone qua WMI | Bình thường với nhiều laptop; có thể chọn cảm biến khác ở menu |
| Cổng 8787 bị chiếm | Có ứng dụng khác dùng cổng này | `start-windows.bat --port 8899` |

## 7. Đóng gói thành file .exe (tuỳ chọn)

Chạy `build-exe.bat` → tạo ra `dist\ThermalCoreHUD.exe`, chạy được trên máy không cài Python. Vẫn cần đặt thư mục `lhm\` cạnh file exe.

## 8. Kiểm thử

```
python -m unittest discover -s tests -v
```

Bộ test dùng dữ liệu `data.json` mẫu: ThinkPad X1 Yoga Gen 5, laptop có card rời NVIDIA, PC để bàn có SuperIO (định dạng LHM cũ, giá trị °F), cùng Linux hwmon và `nvidia-smi`.
