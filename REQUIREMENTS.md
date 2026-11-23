# isd_omnichat: Yêu cầu chức năng (bản 1)

> Trạng thái: **đã duyệt** (23/11/2026), đã làm xong bản đầu (xem README.md). Mục 6 là các quyết định.
> Nền tảng: Odoo 18, LGPL-3, category **ISD Modules**, tác giả IntelliSyncData.
> Nguồn: app desktop OmniChat (Electron), bản tài liệu và ảnh chụp trong `Omnichat_ISD/`.

---

## 1. Mục tiêu

Một **hộp thư hợp nhất** ngay trong Odoo. Nhân viên trả lời khách từ nhiều kênh và nhiều tài khoản trong cùng một màn hình, giao diện theo tinh thần OmniChat:

- cột kênh bên trái
- danh sách hội thoại
- khung chat
- gõ `\` để chèn tin nhắn mẫu kèm ảnh

**Khác app desktop:** app cũ nhúng trang web của Zalo/Messenger... và đăng nhập bằng tài khoản cá nhân. Trong Odoo không làm vậy được, vì các trang đó chặn nhúng và không cho chèn script. Nên isd_omnichat nhận và gửi tin qua **API chính thức**, tức là dùng **tài khoản doanh nghiệp**:

| Nền tảng | App desktop (cũ) | isd_omnichat |
|---|---|---|
| Zalo | Zalo cá nhân | **Zalo Official Account (OA)** |
| Messenger + Fanpage | Messenger cá nhân, Fanpage qua Business Suite | **Fanpage** (tin nhắn Messenger gửi tới page). Messenger cá nhân và Fanpage gộp thành một loại |
| WhatsApp | WhatsApp Web | **WhatsApp Business Cloud API** (số doanh nghiệp) |
| Telegram | Telegram Web | **Telegram Bot** |
| Shopee | Seller webchat | Giai đoạn 2 (cần app đối tác Shopee Open Platform được duyệt). Bản đầu: lưu tài khoản và nút **Kênh Bán** mở Seller Center |
| TikTok Shop | Seller chat | Giai đoạn 2, tương tự Shopee |

**Bù lại**, những thứ app desktop không có:
- lịch sử chat lưu trên server, ai trong team cũng xem được
- giao hội thoại cho nhân viên
- gắn khách với Liên hệ, tạo Lead CRM
- dùng chung tin nhắn mẫu cho cả team
- không phụ thuộc máy nào

---

## 2. Vai trò

| Nhóm | Được làm gì |
|---|---|
| **Omnichat Agent** | Xem và trả lời hội thoại của các tài khoản mình được gán; dùng tin nhắn mẫu; tạo tin nhắn mẫu riêng |
| **Omnichat Manager** | Mọi tài khoản và hội thoại; thêm/sửa/ẩn tài khoản kênh; gán nhân viên; quản lý tin nhắn mẫu dùng chung |

---

## 3. Giao diện (menu **Omnichat**)

Một màn hình chính, bố cục như ảnh `01-workspace`:

| Vùng | Nội dung |
|---|---|
| **Cột kênh** (trái, màu xanh) | Mục **Tất cả**, rồi từng tài khoản: avatar hoặc chữ tắt, huy hiệu nền tảng (Z, M, W, Tg, S, T), số chưa đọc. Dưới cùng: nút **Cài đặt** và bật/tắt âm báo |
| **Danh sách hội thoại** | Tên tài khoản đang chọn và số chưa đọc. Ô tìm theo tên, SĐT, nhãn. Lọc: Chưa đọc / Của tôi / Đã xong. Mỗi dòng: avatar, tên khách, tin cuối, giờ, số chưa đọc |
| **Khung chat** | Đầu khung: tên khách, nhãn (VIP...), kênh, nút gắn **Liên hệ** / **Tạo Lead**, giao cho nhân viên, đánh dấu **Đã xong**. Tin nhắn dạng bong bóng (khách bên trái, mình bên phải, dấu ✓✓ khi đã gửi, ✕ khi lỗi kèm nút gửi lại), ảnh xem trước |
| **Ô soạn tin** | Enter để gửi, Shift+Enter xuống dòng. Nút đính kèm ảnh. Gõ `\` để mở **Tin nhắn nhanh** (↑↓, Enter), lọc theo phím tắt, hiện số ảnh kèm |

**Cài đặt** mở một hộp thoại có tab như ảnh `02-accounts` và `04-snippets`:
- **Tài khoản:** nhóm theo nền tảng; mỗi tài khoản có tên gọi nhớ, avatar, nút **Ẩn**, **Xoá**, **Kênh Bán** (Shopee/TikTok), và form nhập thông tin kết nối API.
- **Tin nhắn mẫu:** phím tắt tối đa 20 ký tự, nội dung, tối đa **6 ảnh**; sửa, xoá.
- Tab **Hệ thống** của app cũ bỏ đi, vì không còn dữ liệu cục bộ để "chuyển nhà" hay "nạp dữ liệu". Ngôn ngữ theo ngôn ngữ của user Odoo (Anh/Việt).

---

## 4. Chức năng chi tiết

### F1. Tài khoản kênh
- Nhiều tài khoản cho mỗi nền tảng. Mỗi tài khoản có thông tin kết nối riêng:
  - Zalo OA: App ID, Secret, OA token và refresh token, tự làm mới token
  - Fanpage: Page ID, Page Access Token, App Secret
  - WhatsApp: Phone Number ID, token
  - Telegram: Bot token
- Mỗi tài khoản có **địa chỉ webhook riêng** để dán vào trang developer của nền tảng; có nút **Kiểm tra kết nối**. Telegram thì tự đăng ký webhook.
- Webhook kiểm tra chữ ký hoặc secret của nền tảng; mỗi lần nhận ghi log để tra lỗi.
- **Ẩn** tài khoản: không hiện trên cột kênh, vẫn nhận tin. **Lưu trữ**: ngừng nhận.
- Gán **nhân viên** cho từng tài khoản.

### F2. Hội thoại và tin nhắn
- Tin đến tạo hoặc mở lại hội thoại theo (tài khoản, mã người gửi). Tự lấy tên và avatar khách nếu API cho phép.
- Tự gắn **Liên hệ** theo SĐT/email nếu có, hoặc tạo liên hệ khi nhân viên bấm.
- Nhận: văn bản, ảnh, file, sticker (hiện dạng chữ "[sticker]").
- Gửi: văn bản, ảnh, file. Ảnh của tin nhắn mẫu được gửi thành tin ảnh, không cần dán clipboard như app cũ.
- Chưa đọc tính theo hội thoại; mở hội thoại là đã đọc.
- Trạng thái hội thoại: Mới / Đang xử lý / Đã xong; tin mới đến thì tự mở lại.
- **Giới hạn của nền tảng** (ghi rõ khi gửi lỗi):
  - Facebook và WhatsApp chỉ cho trả lời trong **24 giờ** kể từ tin cuối của khách
  - Zalo OA có giới hạn tin tư vấn theo gói OA

### F3. Thời gian thực và thông báo
- Tin mới đẩy ngay qua bus của Odoo, không cần tải lại trang.
- **Âm báo** và **thông báo trình duyệt**, có bật/tắt theo từng user.
- Biểu tượng Omnichat trên thanh trên cùng có tổng số chưa đọc.

### F4. Tin nhắn mẫu
- Phím tắt không trùng, tối đa 20 ký tự, không dấu cách. Nội dung có thể chèn `{name}` (tên khách) và `{agent}` (tên nhân viên).
- Tối đa 6 ảnh mỗi mẫu.
- Loại **Dùng chung** (Manager tạo) và **Của tôi**.

### F5. Liên kết Odoo
- Từ hội thoại mở hoặc tạo **Liên hệ**; trên form Liên hệ có nút xem các hội thoại.
- Có cài `crm` thì có thêm nút **Tạo Lead**, kèm nội dung hội thoại.

---

## 5. Kỹ thuật (tóm tắt)

- **Model:**
  - `omnichat.account`: tài khoản kênh
  - `omnichat.conversation`: hội thoại
  - `omnichat.message`: tin nhắn, ảnh/file là `ir.attachment`
  - `omnichat.quick.reply`: tin nhắn mẫu
  - `omnichat.webhook.log`: log webhook
- **Kênh:** mỗi nền tảng một lớp provider (nhận webhook, gửi tin, lấy hồ sơ), nên thêm Shopee/TikTok về sau không phải sửa phần còn lại.
- **Webhook:** `/omnichat/webhook/<nền tảng>/<mã bí mật của tài khoản>`.
- **Gửi tin:** gọi API ngay khi bấm gửi; lỗi thì đánh dấu tin lỗi và hiện lý do.
- **Giao diện:** client action OWL, realtime qua `bus.bus`.
- **Phụ thuộc:** `base`, `mail`, `bus`, `web`, `contacts`. CRM là tuỳ chọn.
- **Không phụ thuộc isd_chatbot.**
  - Lưu ý: Facebook chỉ cho **một** địa chỉ webhook trên mỗi App. Cùng một Fanpage không nên vừa nối isd_chatbot vừa nối isd_omnichat bằng cùng một App.
- **Kiểm thử:**
  - nhận webhook từng nền tảng (có kiểm tra chữ ký)
  - gửi tin (giả lập API)
  - tạo và mở lại hội thoại
  - đếm chưa đọc
  - tin nhắn mẫu (phím tắt trùng, tối đa 6 ảnh)
  - phân quyền Agent/Manager
  - giao diện chạy được trong trình duyệt

---

## 6. Đã chốt

| # | Câu hỏi | Quyết định |
|---|---|---|
| 1 | Kênh | Làm **một lần đủ 6**: Zalo OA, Fanpage, WhatsApp, Telegram, **Shopee, TikTok Shop**. Shopee/TikTok làm theo tài liệu API chính thức, cần app đối tác được duyệt mới chạy thật |
| 2 | Agent thấy gì | Chỉ hội thoại của tài khoản được gán; Manager thấy hết |
| 3 | AI | **Có**: tài khoản bật "AI tự trả lời" thì hội thoại mới do AI (isd_ai_chatbot) trả lời. Khách bấm nút **"Gặp nhân viên"** (Facebook, WhatsApp, Telegram, Zalo có nút) hoặc gõ câu như "gặp nhân viên" thì chuyển sang người. Nhân viên nhắn là tự chuyển sang người. Hội thoại **Đã xong** thì lần sau AI tiếp nhận lại. Không cài isd_ai_chatbot thì tính năng này ẩn đi |
| 4 | Credit | Thêm vào isd_credit_app kiểu giá **"X credit mỗi N lần"** (ví dụ 1 credit / 50 tin). Đặt giá: thao tác Tạo trên `omnichat.message`, điều kiện Chiều = Gửi đi. Hết credit thì AI ngừng trả lời và chuyển hội thoại cho nhân viên; tin khách gửi đến không bao giờ bị chặn |
| 5 | Deploy | Làm xong rồi đưa repo, lên main + ehub |
