from app import app
from extensions import db
import models  # noqa: F401


def create_tables():
    with app.app_context():
        db.create_all()

        inspector = db.inspect(db.engine)
        return inspector.get_table_names()


def main():
    tables = create_tables()

    print("Database tables created successfully.")
    print(f"Database: {app.config['SQLALCHEMY_DATABASE_URI']}")
    print(f"Tables: {', '.join(tables) if tables else 'none'}")


if __name__ == "__main__":
    main()
