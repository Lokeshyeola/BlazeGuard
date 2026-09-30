from sqlalchemy import text

from .database import Base, engine
from .models import (
    ApiKey,
    REQUEST_IDEMPOTENCY_INDEX,
    Request,
    WAITING_QUEUE_POSITION_INDEX,
)


def create_tables(target_engine=engine):
    with target_engine.begin() as connection:
        table_exists = connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='requests'"
        ).first()
        if table_exists:
            columns = {
                row[1]
                for row in connection.exec_driver_sql("PRAGMA table_info(requests)")
            }
            if "idempotency_key" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE requests ADD COLUMN idempotency_key VARCHAR(128)"
                )
            if "request_method" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE requests ADD COLUMN request_method "
                    "VARCHAR(10) NOT NULL DEFAULT 'GET'"
                )
            additions = {
                "attempt_count": "INTEGER NOT NULL DEFAULT 0",
                "failure_category": "VARCHAR(32)",
                "failure_message": "VARCHAR(300)",
                "failure_at": "DATETIME",
                "upstream_status_code": "INTEGER",
            }
            for column, declaration in additions.items():
                if column not in columns:
                    connection.exec_driver_sql(
                        f"ALTER TABLE requests ADD COLUMN {column} {declaration}"
                    )
            index_exists = connection.exec_driver_sql(
                "SELECT 1 FROM sqlite_master WHERE type='index' "
                "AND name='uq_requests_waiting_queue_position'"
            ).first()
            if not index_exists:
                rows = connection.exec_driver_sql(
                    "SELECT id, queue_position FROM requests "
                    "WHERE status IN ('WAITING', 'PROCESSING') "
                    "ORDER BY queue_position, created_at, id"
                ).all()
                if len({position for _, position in rows}) != len(rows):
                    for position, (request_id, _) in enumerate(rows, start=1):
                        connection.execute(
                            text("UPDATE requests SET queue_position = :position WHERE id = :id"),
                            {"position": position, "id": request_id},
                        )
    Base.metadata.create_all(bind=target_engine)
    WAITING_QUEUE_POSITION_INDEX.create(bind=target_engine, checkfirst=True)
    REQUEST_IDEMPOTENCY_INDEX.create(bind=target_engine, checkfirst=True)


if __name__ == "__main__":
    create_tables()
    print("Database tables created successfully.")
