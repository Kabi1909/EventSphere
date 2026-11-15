import json
from typing import Any
from sqlalchemy.orm import Session
from app.models import AgentBelief
from app.agents.protocol import AgentMessage, Performative

class BaseAgent:
    name: str = "BaseAgent"

    def get_belief(self, db: Session, key: str, default: Any = None) -> Any:
        row = db.query(AgentBelief).filter_by(agent_name=self.name, key=key).first()
        if not row:
            return default
        return json.loads(row.value_json)

    def set_belief(self, db: Session, key: str, value: Any) -> None:
        row = db.query(AgentBelief).filter_by(agent_name=self.name, key=key).first()
        serialized = json.dumps(value, default=str)
        if row:
            row.value_json = serialized
        else:
            row = AgentBelief(agent_name=self.name, key=key, value_json=serialized)
            db.add(row)
        db.flush()

    def increment_belief(self, db: Session, key: str, amount: int = 1) -> int:
        current = int(self.get_belief(db, key, 0))
        new_val = current + amount
        self.set_belief(db, key, new_val)
        return new_val

    def handle_message(self, db: Session, msg: AgentMessage, bus: Any) -> AgentMessage:
        raise NotImplementedError

    def reply(self, msg: AgentMessage, performative: Performative, content: dict) -> AgentMessage:
        return AgentMessage(
            conversation_id=msg.conversation_id,
            sender=self.name,
            receiver=msg.sender,
            performative=performative,
            action=msg.action,
            content=content,
        )
