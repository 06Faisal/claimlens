"""Local UI demo without an API key: serves the app with a canned MockLLM (paper's room-rent example). Not for production."""
import sys
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import uvicorn  # noqa: E402
from app.engine import Facts  # noqa: E402
from app.main import create_app  # noqa: E402
from app.mock_llm import MockLLM  # noqa: E402
from app.schemas import Cited, Rules  # noqa: E402

llm = MockLLM(Facts(procedure="knee replacement", diagnosis="osteoarthritis", pre_existing=False, coverage_months=0),
              Rules(sum_insured=Cited(value=D(500000), clause_id="starcare-gold:2.1"),
                    room_rent_cap_per_day=Cited(value=D(5000), clause_id="starcare-gold:3.1")))
uvicorn.run(create_app(llm), host="127.0.0.1", port=8000)
