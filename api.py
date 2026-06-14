from fastapi import FastAPI, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select,update
from fastapi.responses import FileResponse
from Database import get_db, init_db
from Database.models import Conversation, Message
from schemas import ConversationCreate, ConversationResponse, MessageSend, MessageResponse
from chatbot import chat
from contextlib import asynccontextmanager
from chatbot import generate_title
from search import web_search
from search import deep_research
from typing import Optional
from pydantic import BaseModel
from reviewer import review_code
import asyncio
from fastapi import BackgroundTasks
from fastapi.responses import StreamingResponse
from chatbot import stream_chat,generate_title,chat
import json
from sqlalchemy import delete
from fastapi import UploadFile,File,Response
from transcribe import transcribe_audio


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(lifespan=lifespan)


class Request(BaseModel):
    code: str
    question: Optional[str] = ""


from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/conversation")
async def create_conversation(data: ConversationCreate, db: AsyncSession = Depends(get_db)):
    conversation = Conversation(title=data.title)
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation

@app.get("/conversations")
async def get_conversation(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Conversation).order_by(Conversation.created_at.desc()))
    return result.scalars().all()

@app.get("/conversation/{conversation_id}")
async def get_conversation(conversation_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    return result.scalars().all()

@app.post("/chat")
async def send_message(data: MessageSend, db: AsyncSession = Depends(get_db)):
    # print(f"Received: web_search={data.web_search}")
    #fetch history
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == data.conversation_id)
        .order_by(Message.created_at)
    )
    messages = result.scalars().all()
    history = [{"role": m.role,"content":m.content} for m in messages]

    if len(history) == 0:
        generated_title = await generate_title(data.message)
        await db.execute(
            update(Conversation)
            .where(Conversation.id == data.conversation_id)
            .values(title=generated_title)
        )
        await db.commit()

    #save user message
    user_msg = Message(conversation_id=data.conversation_id,role="user",content=data.message)
    db.add(user_msg)
    await db.commit()

    context = ""
    if data.web_search:
        context = web_search(data.message)
        # print(f"RESULTS: {context}")

    if data.deep_research:
        context = await deep_research(data.message)

    #call groq
    response = await chat(history,data.message,context=context)


    #save assistant message
    assistant_msg = Message(conversation_id=data.conversation_id,role="assistant",content=response)
    db.add(assistant_msg)
    await db.commit()

    return {"conversation_id":data.conversation_id,"role":"assistant","content":response}


@app.delete("/conversation/{conversation_id}")
async def delete_conversation(conversation_id: str, db: AsyncSession = Depends(get_db)):
    await db.execute(
        delete(Message)
        .where(Message.conversation_id == conversation_id)
    )
    await db.execute(
        delete(Conversation)
        .where(Conversation.id == conversation_id)
    )
    await db.commit()
    return {"message":"Conversation Deleted successfully"}



@app.get("/")
def root():
    return FileResponse("index.html")

@app.post("/review")
async def review(request: Request):
    output = await review_code(request.code, request.question)
    return json.loads(output)


@app.post("/chat/stream")
async def chat_stream_route(data: MessageSend, db: AsyncSession = Depends(get_db),background_tasks: BackgroundTasks=BackgroundTasks()):
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == data.conversation_id)
        .order_by(Message.created_at)
    )
    messages = result.scalars().all()
    history = [{"role":m.role,"content":m.content} for m in messages]

    if len(history) == 0:
        generated_title = await generate_title(data.message)
        await db.execute(
            update(Conversation)
            .where(Conversation.id == data.conversation_id)
            .values(title=generated_title)
        )
        await db.commit()

    user_msg = Message(conversation_id=data.conversation_id,role="user",content=data.message)
    db.add(user_msg)
    await db.commit()


    context = ""
    if data.web_search:
        context = web_search(data.message)
    if data.deep_research:
        context = await deep_research(data.message)


    full_response = []

    async def generate():
        async for token in stream_chat(history,data.message,context=context):
            full_response.append(token)
            yield f"data: {token}\n\n"
        yield "data: [DONE]\n\n"

    async def save_to_db():
        await asyncio.sleep(1)
        assistant_msg = Message(
            conversation_id=data.conversation_id,
            role="assistant",
            content="".join(full_response)
        )
        db.add(assistant_msg)
        await db.commit()

    background_tasks.add_task(save_to_db)

    return StreamingResponse(generate(),media_type="text/event-stream",background=background_tasks)


@app.post("/transcribe")
async def transcribe_endpoint(audio: UploadFile = File(...)):
    audio_bytes = await audio.read()
    text = await transcribe_audio(audio_bytes,audio.filename)
    return {"text": text}


from speak import text_to_speach

class SpeakRequest(BaseModel):
    text: str

@app.post("/speak")
async def speak(request: SpeakRequest):
    return await text_to_speach(request.text)