from openai import AsyncOpenAI
from config import config
from logger import logger
from typing import Optional, Callable, List, Dict

# Initialize Minimax client (using OpenAI compatible interface)
minimax_client = AsyncOpenAI(
    api_key=config["minimax"]["api_key"],
    base_url=config["minimax"]["base_url"] or "https://api.minimax.chat/v1"
)


async def call_minimax(
    text: str,
    model: str = "abab6.5s-chat",
    temperature: float = 0.7,
    max_tokens: Optional[int] = 400,
    messages: Optional[List[Dict[str, str]]] = None
) -> str:
    """Call Minimax 2.1 model"""
    try:
        # Build message list
        message_list = messages or [
            {"role": "user", "content": text}
        ]
        
        # Call Minimax API
        response = await minimax_client.chat.completions.create(
            model=model,
            messages=message_list,
            temperature=temperature,
            max_tokens=max_tokens
        )
        
        # Extract reply content
        reply = response.choices[0].message.content
        
        if not reply:
            raise ValueError("No response from Minimax API")
        
        return reply
    except Exception as error:
        logger.error(f"Error calling Minimax API: {error}", exc_info=True)
        raise RuntimeError(f"Minimax API error: {str(error)}")


async def call_minimax_stream(
    text: str,
    model: str = "abab6.5s-chat",
    temperature: float = 0.7,
    on_chunk: Optional[Callable[[str], None]] = None
) -> str:
    """Stream call Minimax 2.1 model"""
    try:
        messages = [
            {"role": "user", "content": text}
        ]
        
        # Call Minimax API (stream)
        stream = await minimax_client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            stream=True
        )
        
        full_response = ""
        
        # Process stream response
        async for chunk in stream:
            content = chunk.choices[0].delta.content or ""
            if content:
                full_response += content
                if on_chunk:
                    on_chunk(content)
        
        return full_response
    except Exception as error:
        print(f"Error calling Minimax API (stream): {error}")
        raise RuntimeError(f"Minimax API error: {str(error)}")

