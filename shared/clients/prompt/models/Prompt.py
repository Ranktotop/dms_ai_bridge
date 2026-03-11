from dataclasses import dataclass, field


@dataclass
class PromptConfigMessage:
    role: str
    content: str

    def to_llm_message_dict(self):
        return {
            "role": self.role,
            "content": self.content,
        }

@dataclass
class PromptConfig:
    id:str
    stage:str
    messages: list[PromptConfigMessage] = field(default_factory=list)
    schema: dict|None = None
    variables: list[str] = field(default_factory=list)