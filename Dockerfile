FROM python:3.12-slim

WORKDIR /app

# ffmpeg — 면접관 TTS 음량 정규화(loudnorm)에 필요.
# 없으면 정규화가 조용히 스킵돼 목소리마다 음량이 제각각이 된다(app/interview/tts.py).
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# CPU 전용 PyTorch 및 핵심 라이브러리 설치 (CUDA 원천 차단)
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch --extra-index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir transformers && \
    pip install --no-cache-dir -r requirements.txt


COPY . .

EXPOSE 8000

# 💡 안전을 위해 python -m 모듈 방식으로 실행 경로 문제를 방지합니다.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
