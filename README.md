# Omnichat (isd_omnichat)

Hộp thư hợp nhất trong Odoo 18 cho **Zalo OA, Fanpage Facebook, WhatsApp, Telegram, Shopee và TikTok Shop**. Mỗi nền tảng nối được nhiều tài khoản. Có tin nhắn mẫu `\phimtat` kèm ảnh, AI tự trả lời và nút "Gặp nhân viên".

Đây là bản Odoo của app desktop OmniChat (Electron). Bố cục giữ như app cũ: cột kênh, danh sách hội thoại, khung chat, hộp Cài đặt có tab Tài khoản và Tin nhắn mẫu.

Phụ thuộc: `base`, `mail`, `bus`, `web`, `contacts`. Không bắt buộc nhưng dùng được thêm:
- `isd_ai_chatbot`: AI tự trả lời
- `isd_credit_app`: tính credit theo tin gửi đi
- `crm`: tạo Lead từ hội thoại

## Khác app desktop

App desktop mở trang web của Zalo/Messenger... bằng **tài khoản cá nhân**. Trình duyệt không cho làm vậy, nên Omnichat nhận và gửi tin qua **API chính thức**, tức là dùng **tài khoản doanh nghiệp**:

| Nền tảng | Cần có | Luật của nền tảng |
|---|---|---|
| Zalo | **Zalo OA** và một app trên developers.zalo.me | Chỉ nhắn được cho người đã tương tác với OA; hạn mức tin tư vấn theo gói OA |
| Facebook | **Fanpage** và một app Meta có quyền `pages_messaging` | Chỉ trả lời được trong **24 giờ** kể từ tin cuối của khách |
| WhatsApp | **WhatsApp Business Cloud API** (số doanh nghiệp) | Trong 24 giờ chat tự do; ngoài 24 giờ nền tảng từ chối, cần mẫu tin được duyệt |
| Telegram | **Bot** tạo từ @BotFather | Khách phải bấm Start với bot trước |
| Shopee | App **Shopee Open Platform** được duyệt quyền Seller Chat, shop đã uỷ quyền | Chỉ gửi chữ và ảnh |
| TikTok Shop | App **TikTok Shop Partner** có quyền Customer Service, shop đã uỷ quyền | Chỉ gửi chữ và ảnh |

Bỏ so với app cũ: tab Hệ thống (chuyển nhà, nạp dữ liệu) vì dữ liệu nằm trên server; nút đổi ngôn ngữ vì ngôn ngữ theo user Odoo (Anh/Việt).

Thêm so với app cũ:
- lịch sử chat lưu trên server, cả team cùng xem
- giao hội thoại cho nhân viên, gắn nhãn, đánh dấu Xong
- gắn Liên hệ, tạo Lead CRM
- tin nhắn mẫu dùng chung cho team
- AI tự trả lời

## Phân quyền

| Nhóm | Được làm |
|---|---|
| **Agent** | Thấy và trả lời hội thoại của các tài khoản **được gán**; dùng tin nhắn mẫu; tạo tin nhắn mẫu riêng |
| **Manager** | Mọi tài khoản và hội thoại; thêm, sửa, ẩn tài khoản; gán nhân viên; tin nhắn mẫu dùng chung |

Thông tin kết nối (token, secret) chỉ Manager đọc được.

## Kết nối một tài khoản

**Omnichat → Cấu hình → Tài khoản kênh → Mới**, hoặc nút ⊞ dưới cột kênh → **Thêm tài khoản**.

1. Đặt tên (ví dụ "Zalo Kinh Doanh 1"), chọn nền tảng, chọn **Nhân viên** được thấy tài khoản.
2. Tab **Kết nối**: nhập thông tin của nền tảng (các ô đổi tên theo nền tảng).
3. Chép **Webhook URL** dán vào trang developer của nền tảng (Telegram không cần, xem bên dưới).
4. Bấm **Kiểm tra kết nối**.

| Nền tảng | Nhập | Webhook |
|---|---|---|
| Zalo OA | App ID, App Secret Key, **OA Secret Key** (phần Webhook của app), OA Access Token, Refresh Token | Dán URL, bật các sự kiện `user_send_*` (và `oa_send_*` nếu muốn thấy tin trả lời từ app Zalo) |
| Facebook | Page ID, Page Access Token, App ID, App Secret, **Verify Token** (tự đặt) | Dán URL và Verify Token, đăng ký `messages`, `messaging_postbacks`, `message_echoes` cho page |
| WhatsApp | Phone Number ID, Access Token, App Secret, Verify Token | Dán URL và Verify Token, đăng ký `messages` |
| Telegram | Bot Token | **Kiểm tra kết nối** tự đăng ký webhook kèm secret |
| Shopee | Partner ID, Partner Key, Shop ID, Access/Refresh Token; API Host để trống (hoặc host test) | Dán URL vào Push Mechanism, bật Webchat push |
| TikTok Shop | App Key, App Secret, Shop ID, Access/Refresh Token; **Shop Cipher** tự lấy khi Kiểm tra kết nối | Dán URL, đăng ký sự kiện **NEW_MESSAGE (14)** |

- Mỗi tài khoản có địa chỉ webhook riêng, có mã bí mật trong đường dẫn. Mọi lần gọi đều được kiểm tra chữ ký hoặc secret của nền tảng; sai thì ghi log **Bị từ chối** và bỏ qua.
- Token của Zalo, Shopee, TikTok hết hạn nhanh: cron **Omnichat: làm mới token các kênh** chạy mỗi 6 giờ; gặp token hết hạn khi gửi thì tự làm mới và gửi lại một lần.
- **Log webhook** (Cấu hình) lưu 30 ngày mọi lần nền tảng gọi vào, để tra vì sao tin không về.
- **Ẩn** tài khoản: không hiện ở cột kênh, vẫn nhận tin. **Lưu trữ**: ngừng nhận tin.
- Shopee/TikTok có nút **Kênh Bán** mở Seller Center.

> Facebook chỉ cho **một** địa chỉ webhook trên mỗi App. Fanpage đang nối với isd_chatbot hoặc isd_ai_chatbot bằng cùng App thì phải dùng App khác, hoặc bỏ nối ở module kia.

## Hộp thư

**Omnichat → Hộp thư** (`/odoo/omnichat`).

- **Cột kênh:** "Tất cả" và từng tài khoản, có số hội thoại chưa đọc. Dưới cùng: bật/tắt âm báo, bật/tắt thông báo màn hình, **Cài đặt**.
- **Danh sách:** tìm theo tên, SĐT, nhãn, liên hệ, nội dung tin cuối. Lọc **Đang mở / Chưa đọc / Của tôi / AI / Xong**.
- **Khung chat:** đổi tên khách (bấm vào tên), chuyển **AI / Nhân viên**, **Giao cho**, **Nhãn**, **Liên hệ** (gắn hoặc tạo liên hệ theo SĐT), **Lead CRM**, **Xong / Mở lại**.
- **Soạn tin:** Enter gửi, Shift+Enter xuống dòng, 📎 đính kèm ảnh/tệp. Tin lỗi hiện lý do và nút **Gửi lại**.
- Tin mới về ngay không cần tải lại, có âm báo và thông báo màn hình (khi đang ở tab khác). Biểu tượng chat trên thanh trên cùng có tổng số chưa đọc.
- Facebook/WhatsApp quá 24 giờ từ tin cuối của khách thì khung chat có dòng nhắc.
- Điện thoại: danh sách và khung chat hiện lần lượt, có nút quay lại.

## Tin nhắn mẫu

Cài đặt → **Tin nhắn mẫu**, hoặc menu **Tin nhắn mẫu**.

- Phím tắt tối đa 20 ký tự (chữ, số, `-`, `_`), nội dung, tối đa **6 ảnh**.
- `{name}` thành tên khách, `{agent}` thành tên nhân viên.
- Trong ô chat gõ `\` (ví dụ `\xin`), chọn bằng ↑↓ và Enter. Nội dung chèn vào ô chat, ảnh vào hàng chờ; bấm Gửi thì gửi chữ trước rồi từng ảnh.
- Manager tạo mẫu **dùng chung**; nhân viên tạo mẫu **riêng**. Trùng phím tắt thì mẫu riêng được ưu tiên.

## AI tự trả lời

Cần cài **AI Chatbot (by ISD)**. Bật trong tab **AI tự trả lời** của tài khoản.

1. Hội thoại mới do AI trả lời, dùng kho kiến thức và Q&A của AI Chatbot. Câu trả lời bỏ định dạng markdown cho hợp app chat.
2. Dưới câu trả lời có nút **"Gặp nhân viên"** (Facebook, WhatsApp, Telegram, Zalo). Khách bấm nút, gõ đúng chữ đó, hoặc gõ câu kiểu "cho em gặp nhân viên" thì hội thoại chuyển sang người và khách nhận **Câu trả lời khi chuyển nhân viên**.
3. Nhân viên nhắn vào là tự chuyển sang người. Khách gửi ảnh/tệp cũng chuyển sang người vì AI không đọc được ảnh.
4. AI lỗi hoặc hết credit thì chuyển sang người và gửi **Khi AI không trả lời được** (nếu có nhập).
5. Đánh dấu **Xong** thì lần sau khách nhắn, AI tiếp nhận lại.

## Tính credit (isd_credit_app)

Không cần code thêm. Trong **Credit → Cấu hình → Prices**:

- Module: **Omnichat**, Thao tác: **Create**, Model: **Omnichat Message**
- Chỉ khi: **Direction = Sent**
- Giá: ví dụ **1 credit mỗi 50 lần**

Tin khách gửi đến không bao giờ bị tính hay bị chặn. Tin nhân viên trả lời từ app gốc (echo), tin chuyển nhân viên và tin "khi AI không trả lời được" không tính credit. Hết credit thì nhân viên không gửi được, còn AI chuyển hội thoại cho người.

## Kiểm thử

```bash
odoo-bin -c odoo.conf -d <db> --db-filter='^<db>$' -i isd_omnichat \
  --test-enable --test-tags=/isd_omnichat --stop-after-init
```

35 test:
- chữ ký webhook và đọc tin của cả 6 nền tảng
- tự làm mới token Zalo; nút "Gặp nhân viên" của từng nền tảng
- chữ ký API Shopee và TikTok
- nhận tin: không trùng, tải ảnh, echo, tìm liên hệ theo SĐT, WhatsApp báo gửi lỗi
- gửi chữ và ảnh, gửi lỗi rồi gửi lại
- AI trả lời, chuyển nhân viên bằng nút / bằng chữ / khi nhân viên nhắn / khi AI lỗi / khi khách gửi ảnh
- nối AI Chatbot thật
- credit "1 / N tin" và AI chuyển người khi hết credit
- phân quyền Agent/Manager, tin nhắn mẫu
- webhook qua HTTP
- test trình duyệt: hộp thư, gõ `\` chèn mẫu, hộp Cài đặt

Test AI Chatbot và Credit tự bỏ qua nếu DB không cài hai module đó.

**Shopee và TikTok Shop** làm theo tài liệu API chính thức (Shopee Open Platform v2, TikTok Shop Partner API 202309/202606) và có test chữ ký/định dạng, nhưng **chưa chạy với shop thật** vì cần app đối tác được duyệt. Khi có app, kiểm tra lại bằng **Kiểm tra kết nối** và Log webhook.
