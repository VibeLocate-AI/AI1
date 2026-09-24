# VibeLocate AI — Inference Service

هذا هو الـ **AI & NLP Processing Service** المذكور بالفصل 4.1.1 من الـ SRS
(الطبقة الرابعة من المعمارية). خدمة FastAPI مستقلة، بتتصل بـ DeepSeek API،
وما بتلمس قاعدة البيانات الرئيسية ولا الـ Core Backend مباشرة — بس بترجع
JSON منظم والـ Core Backend (Node.js) هو يلي بيكمل الشغل (pgvector search،
تخزين، إلخ) زي ما موصوف بالـ Sequence Diagram.

## البنية

```
app/
  config.py                    # قراءة DEEPSEEK_API_KEY من .env
  schemas.py                   # نماذج البيانات (مطابقة لـ Class Diagram)
  deepseek_client.py           # الاتصال بـ DeepSeek + معالجة الأخطاء
  services/
    intent_recognition.py      # US-07: نص → معايير بحث منظمة
    sentiment_analysis.py      # US-08/US-10: مراجعة → درجة مشاعر + Vibe Report
  routers/
    search.py                  # POST /api/search/ai-contextual
    reviews.py                 # POST /api/reviews/analyze
                                # POST /api/reviews/vibe-report
  tests/
    test_services.py           # اختبارات (بدون الحاجة لمفتاح API حقيقي)
```

## التشغيل

1. ثبّت المتطلبات:
   ```
   pip install -r requirements.txt
   ```

2. انسخ `.env.example` إلى `.env` وحط مفتاح DeepSeek الحقيقي فيه:
   ```
   cp .env.example .env
   ```

3. شغّل السيرفر:
   ```
   uvicorn app.main:app --reload
   ```

4. جرب الـ API التفاعلي على: http://localhost:8000/docs

## الاختبارات

```
pytest app/tests/ -v
```

الاختبارات كلها **mocked** — يعني ما بتحتاج مفتاح API حقيقي ولا اتصال
إنترنت عشان تتأكد إن منطق الـ parsing والـ aggregation صحيح.

## ليش القرارات المعمارية هيك؟ 

- **DeepSeek بس، مش أي نموذج تاني**: محدد بالـ SRS (Limitations 1.4) —
  "No self-hosted AI models... delegated to DeepSeek API".
- **كل استدعاء بيرجع fallback آمن بدل ما يفشل**: NFR3.01 يطلب "graceful
  degradation" لو خدمة الـ AI مش متوفرة.
- **response_format json_object**: عشان نضمن إخراج منظم قابل للـ parsing
  دايماً، بدل ما نعتمد على الموديل يلتزم بالتنسيق من نفسه.
- **temperature=0.1**: لأنه هاي مهمة استخراج بيانات، مش محادثة إبداعية —
  بدنا ثبات بالنتائج.

## الخطوة الجاية

- [ ] نبدّل مفتاح `.env` بمفتاح DeepSeek حقيقي ونختبر مقابل الـ API الفعلي
      (نفس الاختبارات، بس بدون mock — راقب دقة الـ vibe_tags على أمثلة عربي/إنجليزي حقيقية)
- [ ] نتفق مع الـ Backend Developer على شكل الـ contract تبع
      `/api/search/ai-contextual` بالضبط عشان يربطه بـ pgvector
- [ ] نجهز عينة من Yelp Open Dataset ونمررها عبر `/api/reviews/vibe-report`
      لنشوف الأداء على بيانات حقيقية
- [ ] نضيف query embeddings (Sentence-BERT أو DeepSeek embeddings) لتغذية
      الـ semantic matching
