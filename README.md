# Planventure API

Backend REST API cho ứng dụng lập kế hoạch du lịch Planventure.

## Chức năng

- Đăng ký và đăng nhập bằng email/mật khẩu
- Access token và refresh token JWT
- CRUD chuyến đi theo từng người dùng
- Sinh lịch trình mặc định theo khoảng ngày
- SQLite cho phát triển cục bộ, SQLAlchemy và Alembic migrations
- CORS cho frontend React/Vite
- Rate limiting và health check database
- Bộ kiểm thử tự động và GitHub Actions CI

## Bắt đầu nhanh

```powershell
cd planventure-api
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .sample.env .env
python -m flask --app app db upgrade
python -m flask --app app run --debug
```

API mặc định chạy tại `http://127.0.0.1:5000`.

Xem [tài liệu API](planventure-api/README.md) để biết cấu hình, endpoint, kiểm
thử và cách chạy production.

## License

Dự án được cấp phép theo [MIT License](LICENSE).
