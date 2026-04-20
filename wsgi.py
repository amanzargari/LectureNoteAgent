import os
from lecture_note_agent.web.app import create_app

app = create_app(data_dir=os.getenv("DATA_DIR", "/app/data"))
