import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine, Session
from sqlmodel.pool import StaticPool

from main import app, get_session

@pytest.fixture(name="engine", scope="session")
def engine_fixture():
    engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool, # all requests share in-memory db
    )
    return engine

@pytest.fixture(name="db_init", autouse=True)
def db_init_fixture(engine):
    SQLModel.metadata.create_all(engine)
    yield
    SQLModel.metadata.drop_all(engine)

@pytest.fixture(name="client")
def client_fixture(engine):
    def get_session_override():
        with Session(engine, expire_on_commit=False) as session:
            # expire_on_commit holds objects in-memory while asserting test results
            yield session

    app.dependency_overrides[get_session] = get_session_override
    
    with patch("main.engine", engine): # replace the engine before lifespan
        with TestClient(app) as client:
            yield client

    app.dependency_overrides.clear()
