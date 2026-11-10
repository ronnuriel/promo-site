 # Promo Site – עמוד יחצן לאירועים (GitHub Pages)

עמוד סטטי (עמוד אחד) שמציג אירועים ככרטיסיות, ממיין לפי תאריך, ומפריד בין:
- **אירועים קרובים**
- **Past events** (ארכיון, מוסתר כברירת מחדל)

כל אירוע הוא **תיקייה** תחת `events/` עם:
- `meta.json`
- `cover.jpg`

---

## איך מוסיפים אירוע (הדרך הכי פשוטה – עם האפליקציה) ✅

### 1) יוצרים תיקיית אירוע במחשב
1. פתח את האפליקציה:
   `tools/EventFolderMaker.app`
2. מלא:
   - שם אירוע
   - תאריך (YYYY-MM-DD)
   - שעה (HH:MM)
   - מיקום (אופציונלי)
   - לינק לכרטיסים
   - קופון (אופציונלי)
   - תיאור (אופציונלי)
3. בחר תמונה (פלייר)

✅ האפליקציה תיצור לך תיקייה חדשה בתוך:
`events/YYYY-MM-DD-slug/`
ובתוכה:
- `meta.json`
- `cover.jpg`

---

### 2) מעלים את האירוע ל־GitHub דרך האתר (בלי Git)
1. היכנס לריפו ב־GitHub
2. היכנס לתיקייה `events/`
3. לחץ **Add file → Upload files**
4. גרור פנימה את התיקייה החדשה שנוצרה (או את שני הקבצים לתוך תיקייה בשם האירוע)
5. למטה תחת **Commit changes**:
   - בחר **Create a new branch for this commit and start a pull request**
6. לחץ **Propose changes**
7. לחץ **Create pull request**

✅ זהו. אחרי Merge ה־GitHub Actions יעדכן אינדקס ויפרסם את האתר.

---

## מחיקת אירוע
1. היכנס לתיקייה של האירוע תחת `events/...`
2. מחק את `meta.json` ואת `cover.jpg`
3. צור branch ופתח PR כמו בהוספה

---

## GitHub Pages (הקמה)
1. Settings → Pages
2. Source = **GitHub Actions**
3. Push ל־`main`

תקבל לינק:
`https://<user>.github.io/<repo>/`

---

## פורמט `meta.json` (אוטומטי מהאפליקציה)
```json
{
  "title": "Event Name",
  "date": "2026-03-07",
  "time": "22:00",
  "location": "Tel Aviv",
  "description": "",
  "ticket_url": "https://...",
  "promoter_url": "",
  "coupon_code": "",
  "image": "cover.jpg"
}

pyinstaller --windowed --name "EventFolderMaker" tools/event_maker_gui.py

rm -rf tools/EventFolderMaker.app
cp -R dist/EventFolderMaker.app tools/EventFolderMaker.app
