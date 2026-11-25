# Telegram PC Vietnamese Auto-Translate

Công cụ giúp **Telegram Desktop** trên Windows 11 hiển thị nút **Translate / Show Original** trong các nhóm ngoại ngữ (đặc biệt nhóm tiếng Trung) và dịch đè trực tiếp sang **tiếng Việt** ngay trong bong bóng chat mà không cần Telegram Premium và không chỉnh sửa file `Telegram.exe` gốc trên đĩa.

## Tính năng chính

- **Mở khóa nút dịch tại chỗ (`vi_launcher.exe`)**: Khởi chạy `Telegram.exe` ở trạng thái tạm dừng, dò chữ ký động RTTI/AOB hàm `HistoryView::TranslateTracker::setup()` trong RAM và mở khóa riêng cổng hiển thị thanh dịch, không giả mạo trạng thái Premium toàn cục.
- **Tự động bật proxy khi mở shortcut**: Double-click `Telegram (VI)` trên Desktop sẽ tự kiểm tra và khởi chạy nền `translator_proxy` nếu chưa chạy, sau đó mở/patch `Telegram.exe` (tự khởi động lại nếu Telegram đang chạy bản chưa patch).
- **Proxy dịch cục bộ + Cache SQLite (`127.0.0.1:18765`)**:
  - Hỗ trợ **AI (`OpenAI-compatible` / `HMS Router` / `Google Gemini REST`)** kèm từ điển thuật ngữ công nghệ/săn deal Trung và tự fallback sang Google miễn phí nếu AI lỗi/timeout.
  - Hỗ trợ mặc định **Google Translate miễn phí (`google_gtx`)** bảo toàn URL, `@username`, inline code `` `...` `` và chuẩn hóa tiếng lóng.
  - Single-flight deduplication + cache 2 tầng (RAM LRU + SQLite WAL).
- **Dịch trước tự động (MTProto Pre-Translate)**:
  - Đăng nhập trực tiếp trong Dashboard (quét QR hoặc SĐT + OTP + 2FA) với `api_id`/`api_hash` từ `my.telegram.org`.
  - Chọn các nhóm muốn theo dõi → tự lấy sẵn N tin gần nhất và lắng nghe tin mới đến theo lô 20 tin/lần gọi AI, đổ sẵn vào `data/cache.db` để cuộn xem tức thì (`< 2ms`).
- **Bảng điều khiển Web cục bộ**: Truy cập `http://127.0.0.1:18765/` để cấu hình AI, MTProto Pre-Translate, từ điển, dịch thử và quản lý cache.

## Cài đặt & Sử dụng nhanh

```powershell
# 1. Cài đặt cấu hình Telegram, build vi_launcher.exe và tạo shortcut Desktop "Telegram (VI)"
.\scripts\install.ps1

# (Tùy chọn) Thêm -Autostart nếu muốn tự mở kèm Windows
.\scripts\install.ps1 -Autostart

# 2. Chạy hoặc khởi động lại Telegram kèm patch
.\scripts\start.ps1 -RestartTelegram

# 3. Kiểm tra trạng thái thực tế
.\scripts\status.ps1
```

Hằng ngày chỉ cần mở shortcut **`Telegram (VI)`** ngoài Desktop (hoặc Start Menu).

## Cấu trúc thư mục

- `translator_proxy/`: HTTP server Python 3.14 (`config.py`, `cache.py`, `providers.py`, `pretranslate.py`, `server.py`, `dashboard.py`).
- `shim_mscms/`: Mã nguồn Rust (`pe_patch.rs`, `remote_patch.rs`, `bin/vi_launcher.rs`).
- `scripts/`: Bộ script quản lý vòng đời (`install.ps1`, `start.ps1`, `stop.ps1`, `status.ps1`, `uninstall.ps1`, `config_telegram.py`).
- `tests/` & `shim_mscms/tests/`: Bộ test tự động Python và Rust.

## Kiểm thử & Gỡ bỏ sạch sẽ

```powershell
# Chạy toàn bộ test Python + Rust
python -m unittest discover -s tests -p "test_*.py" -v
cargo test --manifest-path shim_mscms/Cargo.toml

# Gỡ bỏ hoàn toàn khỏi Telegram Desktop (khôi phục experimental_options.json, xóa shortcut & dừng proxy)
.\scripts\uninstall.ps1 -StopTelegram
```
