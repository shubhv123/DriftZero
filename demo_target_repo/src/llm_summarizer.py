import openai
from pydantic import BaseModel, Field


class SummaryPrompt(BaseModel):
    system_instruction: str = "Summarize the transaction dispute concisely."
    user_transcript: str = Field(..., min_length=10)
    temperature: float = 0.2

    def as_openai_messages(self) -> list:
        data = self.dict()
        return [
            {"role": "system", "content": data["system_instruction"]},
            {"role": "user", "content": data["user_transcript"]},
        ]


def generate_dispute_summary(prompt: SummaryPrompt) -> str:
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=prompt.as_openai_messages(),
        temperature=prompt.temperature,
    )
    return response["choices"][0]["message"]["content"]