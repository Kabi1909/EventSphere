import json
from typing import Any, Dict, List
from sqlalchemy.orm import Session
from app.models import AgentMessageLog
from app.agents.protocol import AgentMessage, Performative
from app.agents.base import BaseAgent

class MessageBus:
    def __init__(self):
        self.agents: Dict[str, BaseAgent] = {}
        self.subscribers: List[str] = []  # Agents listening to INFORM broadcasts

    def register(self, agent: BaseAgent, subscribe_inform: bool = False):
        self.agents[agent.name] = agent
        if subscribe_inform and agent.name not in self.subscribers:
            self.subscribers.append(agent.name)

    def _log(self, db: Session, msg: AgentMessage):
        entry = AgentMessageLog(
            conversation_id=msg.conversation_id,
            sender=msg.sender,
            receiver=msg.receiver,
            performative=msg.performative.value,
            action=msg.action,
            content_json=json.dumps(msg.content, default=str),
        )
        db.add(entry)
        db.flush()

    def send(self, db: Session, msg: AgentMessage) -> AgentMessage:
        self._log(db, msg)

        # Broadcast INFORM handling
        if msg.performative == Performative.INFORM and msg.receiver == "ALL":
            for sub_name in self.subscribers:
                if sub_name != msg.sender and sub_name in self.agents:
                    sub_msg = msg.model_copy(update={"receiver": sub_name})
                    self.agents[sub_name].handle_message(db, sub_msg, self)
            return AgentMessage(
                conversation_id=msg.conversation_id,
                sender="MessageBus",
                receiver=msg.sender,
                performative=Performative.INFORM,
                action=msg.action,
                content={"delivered_to": self.subscribers},
            )

        target = self.agents.get(msg.receiver)
        if not target:
            err = AgentMessage(
                conversation_id=msg.conversation_id,
                sender="MessageBus",
                receiver=msg.sender,
                performative=Performative.FAILURE,
                action=msg.action,
                content={"reason": f"Unknown receiver agent: {msg.receiver}"},
            )
            self._log(db, err)
            return err

        response = target.handle_message(db, msg, self)
        self._log(db, response)
        return response
