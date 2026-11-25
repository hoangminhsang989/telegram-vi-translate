# Design Spec: Tự động dịch hội thoại Telegram PC sang tiếng Việt

**Ngày:** 2026-11-25  
**Workspace:** `e:\telegram`  
**Mục tiêu phần mềm:** Telegram Desktop trên Windows 11 (`C:\Users\HMS-PCC\AppData\Roaming\Telegram Desktop\Telegram.exe`, phiên bản `7.4.6.0`, Qt `6.12.0`).

---

## 1. Bối cảnh và Mục tiêu

Người dùng sử dụng ứng dụng **Telegram Desktop chính thức trên Windows 11** để theo dõi các nhóm tiếng Trung (ví dụ `AI 羊毛交流群` với >5.400 thành viên), giao diện ứng dụng đang để tiếng Anh và tài khoản chưa dùng tính năng dịch toàn bộ cuộc trò chuyện của Telegram Premium.

**Mục tiêu cốt lõi:**
- Giữ nguyên ứng dụng Telegram Desktop đang sử dụng hằng ngày (đầy đủ tài khoản, thư mục chat, lịch sử và thói quen thao tác).
- Khi mở một nhóm tiếng Trung, hiển thị **1 nút bật/tắt dịch** ngay trong cuộc trò chuyện.
- Khi bật dịch: toàn bộ tin nhắn văn bản tiếng Trung tự động chuyển sang tiếng Việt và **hiển thị đè trực tiếp lên nội dung tin nhắn trong khung chat** (kích thước bong bóng tin nhắn tự giãn đúng chuẩn, cuộn mượt, tự dịch cả tin cũ khi cuộn lên và tin mới đến).
- Khi tắt dịch: hoàn nguyên ngay lập tức về văn bản tiếng Trung gốc.
- Hoạt động miễn phí mặc định (không cần Telegram Premium hay trả phí bắt buộc), có bộ nhớ đệm cục bộ để phản hồi tức thì, và hỗ trợ tùy chọn nâng cấp sang AI (Gemini / OpenAI-compatible) nếu muốn dịch mượt tiếng lóng công nghệ/nhóm Trung Quốc.
- Cài đặt và gỡ bỏ sạch sẽ, không làm hỏng tệp `Telegram.exe` gốc trên đĩa.

### Không nằm trong phạm vi (Phase 1)
- Chưa dịch chữ nằm bên trong ảnh chụp màn hình hoặc sticker/video (ví dụ ảnh báo cáo chứa chữ Trung).
- Chưa dịch ngược tin nhắn do chính người dùng gõ gửi đi (trừ khi mở rộng ở giai đoạn sau).
- Không giả mạo trạng thái Telegram Premium toàn cục (vì sẽ làm app gửi các yêu cầu Premium bị máy chủ Telegram từ chối hoặc lỗi giới hạn ký tự/emoji).

### Anti-goals (Những điều phải tránh)
- Không dùng cửa sổ đè trong suốt (external overlay) vẽ chữ lên trên cửa sổ Telegram, vì khi bản dịch tiếng Việt dài hơn tiếng Trung, bong bóng chat không tự giãn sẽ bị cắt chữ hoặc lệch vị trí khi cuộn.
- Không biên dịch lại toàn bộ mã nguồn `tdesktop` từ đầu (tốn ~40–50 GB ổ cứng và 1–3 giờ build C++/Qt).
- Không sửa trực tiếp nhị phân `Telegram.exe` trên đĩa theo kiểu ghi đè không thể khôi phục.

---

## 2. Cơ sở kỹ thuật đã kiểm chứng trên `Telegram.exe 7.4.6`

Qua kiểm tra mã nguồn `telegramdesktop/tdesktop` và nhị phân `Telegram.exe 7.4.6.0` trên máy:
1. **Giao diện nút bật/tắt và thay chữ tại chỗ đã có sẵn trong `Telegram.exe`**:
   - `HistoryView::TranslateBar` tạo thanh nút bấm phía trên khung chat với 1 nút chuyển đổi (`Translate to ...` ↔ `Show Original`).
   - `HistoryItem::translatedText()` và `HistoryView::TranslateTracker` xử lý việc thay trực tiếp văn bản dịch vào từng tin nhắn và hoàn nguyên khi tắt mà **không kiểm tra Premium** ở lớp hiển thị.
   - Bộ nhận diện ngôn ngữ (`lib_spellcheck` / CLD3) đã tích hợp sẵn và hoạt động offline trên Windows 11.
2. **Cổng chặn duy nhất nằm ở `HistoryView::TranslateTracker::setup()`**:
   - Hàm này kết hợp `translateChatEnabledValue()` (mặc định `true` trong `Core::Settings`) với `(AmPremiumValue || autoTranslationValue)` (`_1 && (_2 || _3)`).
   - Nhị phân `Telegram.exe` không có `WinVerifyTrust` tự kiểm tra chữ ký, và giữ đầy đủ chuỗi RTTI cho `setup@TranslateTracker@HistoryView@@AEAAXXZ` (đã xác nhận vị trí hàm trong `.text`), cho phép tìm chính xác vị trí hàm bằng signature/RTTI khi khởi chạy.
3. **Cổng thay thế nhà cung cấp dịch đã có sẵn không cần sửa code UI**:
   - `Ui::CreateTranslateProvider` đọc `tdata/experimental_options.json` khóa `"translate-url-template"`.
   - Khi thiết lập mẫu URL trỏ về dịch vụ cục bộ, `Telegram.exe` chuyển toàn bộ yêu cầu dịch (tối đa 20 tin/lượt) qua HTTP GET tới `127.0.0.1` thay vì gọi MTProto của Telegram.
   - Lưu ý giao thức đã xác minh trong `translate_url_provider.cpp`:
     - `Telegram.exe` gọi `TextUtilities::EscapeForHtml()` trước khi URL-encode tham số `%q`, nên máy chủ cục bộ phải giải mã HTML entities (`html.unescape`) trước khi dịch.
     - Kết quả trả về dưới dạng mảng JSON chuẩn `[[["<bản dịch tiếng Việt>"]]]` (`ParseSegmentedArrayResponse`) giúp tránh hoàn toàn lỗi hiểu nhầm định dạng nếu nội dung tin nhắn có chứa ký tự `{` hoặc `[`.
4. **Cơ chế nạp DLL sạch sẽ**:
   - Bảng Static Import của `Telegram.exe` chỉ nạp `mscms.dll`, `kernel32.dll`, `ntdll.dll` (trong đó `mscms.dll` chỉ dùng 4 hàm: `CloseColorProfile`, `GetColorProfileFromHandle`, `WcsCreateIccProfile`, `OpenColorProfileW`, và `System32\mscms.dll` có tổng 123 export).
   - Một `mscms.dll` proxy đặt cạnh `Telegram.exe` (forward toàn bộ 123 export sang `System32\mscms.dll`) được Windows nạp trước khi `SetDefaultDllDirectories` chạy, có thể mở khóa cổng `TranslateTracker::setup()` ngay trong RAM lúc khởi động, đồng thời giữ nguyên 100% file `Telegram.exe` gốc trên đĩa.

---

## 3. Kiến trúc hệ thống

Hệ thống đặt trong `e:\telegram` gồm 4 phần tách biệt rõ trách nhiệm:

```text
[Telegram.exe 7.4.6]
   ├── nạp mscms.dll (shim Rust) ──> forward 123 exports tới System32\mscms.dll
   │                                  └─ mở khóa rpl::combine trong TranslateTracker::setup()
   │                                     (chỉ mở khóa theo dõi dịch chat, không fake Premium toàn cục)
   │
   └── đọc tdata/experimental_options.json ("translate-url-template")
          └─ HTTP GET http://127.0.0.1:18765/translate?from=%f&to=%t&q=%q
                │
                ▼
[Local Translation Proxy - Python 3.14] (e:\telegram\translator_proxy)
   ├── Giải mã HTML + chuẩn hóa text + khóa ngôn ngữ đích = "vi"
   ├── Tra cứu SQLite cache (e:\telegram\data\cache.db) -> trả tức thì nếu đã có
   ├── Hàng đợi gộp/giới hạn đồng thời + deduplicate yêu cầu trùng
   ├── Bộ dịch:
   │     ├── Mặc định: Google Translate endpoint miễn phí (gtx)
   │     └── Tùy chọn: AI (OpenAI-compatible / Gemini) + từ điển thuật ngữ tiếng Trung
   └── Trả về JSON [[["<văn bản tiếng Việt>"]]]
```

### Thành phần 1: `translator_proxy` (Python 3.14, localhost `127.0.0.1:18765`)
- **HTTP Server** (dùng thư viện chuẩn `http.server.ThreadingHTTPServer` hoặc `asyncio` gọn nhẹ, ít phụ thuộc bên ngoài; ưu tiên zero-dependency hoặc ít gói nhất trên Python 3.14 sẵn có):
  - `GET /health`: trả trạng thái dịch vụ, provider đang dùng, số lượng bản dịch đã cache.
  - `GET /translate`: nhận `from`, `to`, `q`; `html.unescape(q)`; bỏ qua hoặc trả nguyên chuỗi nếu chuỗi rỗng/chỉ có emoji/số; ép `target_lang` về `"vi"` khi `force_target_vi: true` trong `config.json` (giải quyết trường hợp UI Telegram đang để tiếng Anh).
- **Caching & Deduplication**:
  - Cache 2 lớp: LRU trong RAM + SQLite (`cache.db` khóa theo `(provider, target_lang, normalized_text_hash)`).
  - Single-flight lock theo key văn bản đang dịch để 2 yêu cầu giống nhau gửi đồng thời chỉ gọi upstream 1 lần.
- **Provider Abstraction**:
  - `GoogleGtxProvider`: gọi endpoint Google Translate miễn phí qua HTTPS với timeout, retry nhẹ và tách đoạn an toàn cho tin nhắn nhiều dòng/dài.
  - `AiTranslateProvider` (tùy chọn bật qua `config.json`): gọi API chuẩn OpenAI/Gemini với system prompt dịch hội thoại nhóm công nghệ/săn deal Trung -> Việt tự nhiên + glossary ánh xạ các cụm phổ biến (ví dụ `0元购`, `羊毛`, `脚本`, `上车`).
- **Xử lý lỗi graceful**: Nếu upstream timeout/lỗi mạng hoặc rate-limit, trả mã lỗi HTTP 502 (hoặc fallback theo cấu hình) để `TranslateTracker` không lưu nhầm chuỗi lỗi vào cache hiển thị, thay vì ghi đè text hỏng lên tin nhắn.

### Thành phần 2: `shim_mscms` (Rust `cdylib` -> `mscms.dll`, compiler `rustc 1.99.1` x64 MSVC)
- **Proxy 123 exports của `mscms.dll`**: Chuyển tiếp đầy đủ toàn bộ export của `System32\mscms.dll` (dùng file `.def` / trampoline chuẩn x64 tải `C:\Windows\System32\mscms.dll` bằng đường dẫn tuyệt đối để tránh vòng lặp tự nạp chính mình).
- **Dynamic Signature Patch (an toàn theo phiên bản)**:
  - Chạy trên thread khởi tạo riêng (hoặc khởi tạo 1 lần an toàn) sau khi `Telegram.exe` đã map xong vào bộ nhớ.
  - Tìm bảng RTTI chứa `setup@TranslateTracker@HistoryView@@AEAAXXZ` -> lần theo `CompleteObjectLocator` / tham chiếu `.text` để định vị hàm `TranslateTracker::setup()` và hàm kết hợp `_1 && (_2 || _3)` (hoặc nguồn `autoTranslationValue` / `AmPremium` bên trong riêng `TranslateTracker::setup()`).
  - Kiểm tra chặt chẽ bytes đặc trưng trước khi ghi (pattern match + bounds check). Nếu khớp: dùng `VirtualProtect` (`PAGE_EXECUTE_READWRITE`) -> vá điều kiện trong bộ nhớ -> `FlushInstructionCache` -> trả quyền bảo vệ cũ. Nếu không khớp (ví dụ Telegram tự cập nhật phiên bản mới): không ghi gì, ghi cảnh báo vào file log và để Telegram chạy bình thường.
  - Ngoài ra hỗ trợ đọc cờ bật/tắt an toàn và ghi log chẩn đoán vào `e:\telegram\logs\shim.log` (hoặc `%LOCALAPPDATA%` nếu chạy độc lập).

### Thành phần 3: Cấu hình `tdata/experimental_options.json`
- Đọc file `C:\Users\HMS-PCC\AppData\Roaming\Telegram Desktop\tdata\experimental_options.json` (tạo mới nếu chưa có, hoặc sao lưu bản cũ trước khi gộp).
- Gộp khóa `"translate-url-template": "http://127.0.0.1:18765/translate?from=%f&to=%t&q=%q"` mà không xóa các tùy chọn thử nghiệm khác của người dùng nếu đã tồn tại.

### Thành phần 4: Bộ script vận hành (`e:\telegram\scripts\`)
- `install.ps1`: kiểm tra/build `mscms.dll`, sao lưu và cập nhật `experimental_options.json`, copy `mscms.dll` vào `C:\Users\HMS-PCC\AppData\Roaming\Telegram Desktop\`.
- `start.ps1`: đảm bảo proxy đang chạy nền trên `127.0.0.1:18765` (kiểm tra `/health`), sau đó mở `Telegram.exe -noupdate` (hoặc nhắc khởi động lại Telegram nếu đang chạy bản chưa nạp shim).
- `stop.ps1` / `status.ps1`: kiểm tra trạng thái proxy + Telegram + log shim; dừng proxy khi không dùng.
- `uninstall.ps1`: gỡ `mscms.dll` khỏi thư mục Telegram Desktop, khôi phục `experimental_options.json` về bản sao lưu ban đầu, tắt proxy.

---

## 4. Hành vi trải nghiệm và các trường hợp biên

1. **Nút bật/tắt dịch**:
   - Thanh `TranslateBar` xuất hiện tự động ở đầu nhóm tiếng Trung (sau khi khung chat có đủ tin nhắn ngôn ngữ khác để bộ nhận diện nhận ra tiếng Trung, ngưỡng mặc định của Telegram là ≥ 6 tin nhắn).
   - Bấm 1 lần để bật dịch đè tiếng Việt toàn khung chat; bấm lại để về bản gốc tiếng Trung.
   - Trong menu `...` trên thanh `TranslateBar`, người dùng vẫn có thể chọn `Translate to -> Vietnamese` hoặc `Do not translate` cho từng nhóm cụ thể nếu muốn tắt riêng một nhóm.
2. **Tin nhắn đang gõ, link, code, emoji**:
   - Proxy giữ nguyên URL, `@username`, đoạn code hoặc emoji thay vì phá cấu trúc; các tin chỉ có emoji/số được `Telegram.exe` bỏ qua sẵn (`isOnlyEmojiAndSpaces()`).
3. **Cập nhật Telegram**:
   - Khởi chạy với `-noupdate` qua `start.ps1` để giữ ổn định; nếu Telegram vẫn cập nhật đè hoặc `mscms.dll` không khớp chữ ký phiên bản mới, shim tự bỏ qua patch một cách an toàn và báo rõ trong log thay vì gây crash.
4. **Dừng / Gỡ sạch**:
   - Chạy `uninstall.ps1` đưa `Telegram Desktop` về trạng thái nguyên bản 100% trong vài giây.

---

## 5. Phần để lại cho giai đoạn lập kế hoạch chi tiết (Implementation Plan)

- Kiểm tra chính xác mã máy quanh `0x14203edae` / `0x14203f530` (hàm kết hợp `_1 && (_2 || _3)` trong `TranslateTracker::setup()`) để chọn điểm patch tối ưu nhất và tổng hợp chữ ký AOB tổng quát (không phụ thuộc địa chỉ tuyệt đối).
- Xác nhận cơ chế forward toàn bộ 123 export của `mscms.dll` bằng `rustc` (`x86_64-pc-windows-msvc`) không gặp vòng lặp tải DLL và không bị thiếu symbol phụ thuộc.
- Kiểm tra hành vi thực tế của `LanguageBox` (`Settings -> Language -> Translate Entire Chats`) xem giá trị mặc định `translateChatEnabled = true` trong `tdata` có bị tắt sẵn hay cần đồng bộ thêm cờ nào không.
