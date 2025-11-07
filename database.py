from sqlmodel import create_engine, Session
from lib.env import env

DATABASE_URL = env.DATABASE_URL
# echo for development and debugging
engine = create_engine(DATABASE_URL, echo=True)


def get_session():
    """
    FastAPI endpoints can depend on this function to establish
    short-lived sessions for each request.
    """
    with Session(engine) as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
