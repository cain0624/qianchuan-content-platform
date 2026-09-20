FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 运行时需注入环境变量：SILICONFLOW_API_KEY（硅基流动视觉模型 key）
EXPOSE 8900
CMD ["sh", "-c", "uvicorn webapp.app:app --host 0.0.0.0 --port ${PORT:-8900}"]
