import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine
from database import get_session
from main import app
from models import Instrument, DailyPrice
