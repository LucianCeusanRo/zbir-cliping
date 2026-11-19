# 🎬 Zbir Cliping – clipuri automate din live-urile lui Zbir → TikTok (#zbirgta6)

Aplicație pentru Windows care stă pornită 24/7 pe PC-ul tău și:

1. **verifică singură** la fiecare minut dacă Zbir (youtube.com/@zbir) e LIVE; când pornește un live nou (ex. „PART 4”), îl prinde automat;
2. **înregistrează live-ul** în bucăți de 5 minute **și ia și ce s-a transmis deja** de la începutul live-ului (din DVR-ul YouTube, max ~12h). Dacă live-ul se oprește și repornește, sau pică netul, completează singură golurile (din DVR sau din VOD după ce se termină live-ul);
3. **transcrie în română** (Whisper large-v3 pe placa RTX 4060 Ti, limba forțată `ro`, diacritice corecte ș ț ă â î);
4. **alege momentele amuzante/virale**: râs, țipete, nervi/rage, înjurături, „nu se poate / doamne / aoleu”, glume cu chatul, hype de giveaway (PS5), chatul care explodează;
5. **face clipuri verticale 9:16 (20–60 s)**: camera lui Zbir sus + jocul jos (detectează automat fața; dacă nu e cameră → tot ecranul pe fundal blurat), **subtitrări mari cuvânt-cu-cuvânt**, hook în primele 2 secunde, titlu în română;
6. **tu le vezi în aplicație** și apeși **„Postează”** (sau pornești **Auto-postare** și le postează singură pe cele mai bune);
7. postează pe TikTok cu **#zbirgta6 mereu** (nu se poate scoate), cu pauză între postări și limită pe zi;
8. **ține minte tot** (`data\state.db`): după restart / pană de curent nu taie și nu postează nimic de două ori.

---

## Instalare (o singură dată, ~15–20 minute)

1. Dezarhivează `zbir-clipper.zip` într-un folder **fără diacritice/spații ciudate**, ex. `C:\ZbirCliping`.
2. Dublu-click pe **`setup.bat`**. Instalează Python 3.12, ffmpeg, Deno, pachetele, Chromium pentru postare și modelul Whisper (~3 GB), rulează testele automate și **creează scurtătura „Zbir Cliping” pe Desktop**.
   *Fără winget, fără `py`, fără admin. Dacă ai deja Python 3.12 instalat (chiar dacă nu e în PATH) îl găsește singur: registru `HKCU/HKLM\Software\Python\PythonCore\3.12`, `%LocalAppData%\Programs\Python\Python312`, `C:\Program Files\Python312`, `C:\Python312`, `where python`. Doar dacă nu găsește nimic descarcă Python de pe python.org. ffmpeg și Deno se pun în folderul `bin\` al programului. Merge din orice folder, și cu spații în cale.*
3. Deschide **„Zbir Cliping”** de pe Desktop → apasă **🔑 Login TikTok** → se deschide Chrome → te loghezi pe contul tău. Se salvează automat (programul NU îți salvează parola; sesiunea stă în `data\tiktok_profile`).

## Folosire

Deschide **Zbir Cliping** (scurtătura de pe Desktop sau `Zbir Cliping.exe`). În fereastră ai:

- sus: dacă Zbir e **LIVE** (cu link), ce face programul acum, butoane **Start/Stop urmărire**, **Pauză postare**, **Auto-postare ON/OFF**, **Login TikTok**;
- **🆕 Clipuri noi**: clipurile gata, cu poză (click = vezi clipul), scor, motiv (râs/strigăt/chat…), descriere editabilă și butoanele **📤 Postează / ⏭ Sari / 🗑 Șterge**;
- **⏳ Coadă postare**: ce urmează să fie postat (și ce a eșuat, cu motivul + **Reîncearcă**);
- **✅ Postate**: istoric cu link „Vezi pe TikTok”;
- **⚙️ Setări**: hashtag-uri, pauză între postări, maxim pe zi, ore de liniște, câte clipuri să taie, layout, vizibilitate; plus **adaugă manual un link** de live/VOD;
- **📜 Log**: tot ce face programul. Problemele apar și sus, în roșu, în română.
- **📊 Progres** (mereu sus, se actualizează la 1 s): cât din live e procesat (%, bară, cât e în urmă față de live, cât mai durează), ce face acum (descarc / transcriu % / caut momente / randez clipul 2/4 % / postez), coada (bucăți, clipuri de postat, următoarea postare) și contoare pe sesiune;
- **🎨 Stiluri**: categoriile de clipuri (Rage, Fail, Reacție șoc, Chat/donații, Giveaway, Cringe, Glume/roast, Momente GTA haotice, Story time, Moment tare) și efectele de editare – fiecare se poate opri; număr de clipuri, viral mediu, buton TOP 5;
- în fiecare tab: sortare (**Cele mai virale primele** implicit / noi / vechi / durată), prag viral minim (slider + ≥50% / ≥40% / toate), filtru pe stil – se țin minte.

> **Urmărirea live-ului pornește automat** când deschizi aplicația (clipurile se fac singure). **Postarea pe TikTok se face doar dacă Auto-postare e ON** (sau când apeși „📤 Postează”).

**Lasă aplicația deschisă** (poți s-o minimizezi). Dacă o închizi, se oprește înregistrarea. Clipurile sunt în `data\clips\`.

## ✂️ Generează clipuri din orice link

Sus în aplicație: lipește un link (YouTube live/VOD, Kick, Twitch, TikTok live replay – orice știe yt-dlp) **sau calea unui fișier** de pe PC (ex. `C:\Videos\live.mp4`), alege opțional **De la / Până la** (ex. `1:05:00` – `1:30:00`) și **câte clipuri** vrei, apoi **✂️ Generează clipuri**. Bara arată progresul (descărcare → transcriere → momente → randare); clipurile apar în **🎞 Toate clipurile**. Urmărirea automată a lui Zbir merge în paralel. Fără interval: maxim 4 ore dintr-un VOD; un link LIVE fără interval = înregistrează următoarele 15 minute.

## Pe fiecare clip

- **🔥 Șansă viral 0–100%** – click pe ea ca să vezi de ce: hook, emoție/volum, cuvinte cheie, chat, durată (+ nota AI dacă ai LLM). E o estimare, nu o garanție.
- **🔄 Refă textul** – titlu + descriere + hashtag-uri noi, altă variantă la fiecare apăsare. **#zbirgta6 e mereu PRIMUL hashtag** (pus automat și verificat încă o dată chiar înainte de postare).
- **✂️ Refă clipul** – mută începutul/sfârșitul cu −1s/+1s și (bifat) îl lipește de început/sfârșit de propoziție sau de o pauză; se randează din nou cu subtitrările potrivite. Merge pentru clipurile noi (programul păstrează o sursă mică ±12 s, max 15 GB, se șterge după postare).
- **📤 Postează / ⏭ Sari / 🗑**.

## Efecte (Setări → Efecte, fiecare se poate opri)

Emoji-uri care apar lângă subtitrare după ce se spune (😂 râs, 🤬 nervi, 😱 șoc, 🔥 acțiune, 💬 chat), cuvântul spus „sare”, culori pe cuvintele cheie (roșu = nervi, verde = râs …), **zoom punch-in + volum mai tare + sunet „boom”** pe țipete/momente tari (sincronizate), tremurat ușor la rage, bară de progres (opțional). Volumul final e adus la **−14 LUFS** cu limitator (fără distorsiuni). Sunetele sunt generate de program (`assets/sfx/make_sfx.py`) → fără probleme de copyright. Fonturi: Montserrat Black (implicit), Anton, Bebas Neue, Archivo Black, Poppins Black, Bangers – vezi `samples/font_comparatie.jpg`.

## Setări importante

| setare | implicit | ce face |
|---|---|---|
| hashtag-uri | `#zbirgta6 #zbir #gta6` + 2 aleatoare din `#gta6romania #fyp #pentrutine #viral #romania #funny` | **#zbirgta6 e mereu primul.** Max 5 în total (setabil) |
| Auto-postare | OFF | OFF = tu apeși „Postează”. ON = postează singură cele mai bune clipuri |
| Ritm postare | **Normal**: 45/zi, 20–30 min ± 4 min | presetări: Sigur (20/zi, 40–60 min), Normal, Agresiv (60/zi, 15–20 min). O singură postare odată |
| prag viral auto-postare | 40% | auto-postarea ia doar clipuri cu șansă virală ≥ prag, cele mai virale primele; momentele aproape identice sunt sărite |
| oprire automată TikTok | – | dacă TikTok Studio arată un avertisment/limită, postarea se oprește 1h, apoi 2h, 4h… (banner roșu cu mesajul exact) |
| folder date | `F:\ZbirCliping\data` | Setări → Disc și curățenie → 📁 Alege folderul (mută fișierele în siguranță) |
| curățenie | păstrează tot | ștergere după postare, ore de păstrare a bucăților brute, zile pentru clipurile sărite, limită disc (GB) |
| pauză între clipurile aprobate de tine | 3 min | ca TikTok să nu te vadă ca spam |
| prag scor / max la 5 min | 0.9 / 3 | mai mic = mai multe clipuri |
| layout | split | split = camera sus + joc jos; blur; fill |
| filtru titlu live | `(?i)gta` | taie doar live-urile cu „GTA” în titlu (gol = toate) |

Setările avansate (model Whisper, zona camerei `cam_box`, cookies YouTube) sunt în `config.yaml`. Modificările tale personale pune-le în
`config.local.yaml` (are prioritate și **nu e suprascris la update**). Setările din aplicație sunt în folderul de date.

## ⬆️ Actualizare

Versiunea apare sus, lângă butonul **⬆️ Update**. La pornire aplicația verifică pe GitHub (`LucianCeusanRo/zbir-cliping`) dacă e o versiune nouă
și arată o bulină. Update-ul face backup la cod (`data\backups\`), înlocuiește doar codul (nu atinge `data`, `.venv`, `bin`, `.env`,
`config.local.yaml`, cookies, sesiunea TikTok), instalează pachetele doar dacă s-a schimbat `requirements.txt`, face un test și repornește;
dacă ceva nu merge, revine automat la versiunea veche. Din consolă (cu aplicația închisă): `update.bat`.

## Opțional: AI (LLM) pentru momente și titluri mai bune

Copiază `.env.example` ca `.env` și pune o cheie (OpenAI / xAI Grok / Groq / OpenRouter) sau folosește **Ollama** local, gratuit.
Cu LLM: notează fiecare moment 0–10 după cât de amuzant e (prompt în română, stilul clipului de 600k), scrie hook + titlu și corectează greșelile de transcriere înainte de subtitrare. Fără LLM merge oricum, pe reguli + titluri gata scrise.

## Dacă apare o problemă

- **„Sign in to confirm you're not a bot”** (YouTube): în `config.yaml` pune `youtube_cookies_from_browser: "firefox"` (sau `edge`/`chrome`, browserul în care ești logat pe YouTube; Chrome trebuie să fie închis).
- **„nu esti logat pe TikTok”** → apasă din nou **Login TikTok**.
- **Postarea eșuează** → motivul apare la clip; poze cu ce a văzut browserul sunt în `data\screens\`. TikTok își schimbă des site-ul – trimite-mi poza și repar.
- **Postare neconfirmată** → verifică în TikTok Studio; programul nu o repostează singur (ca să nu apară dublu).
- Programul nu pornește → rulează `run_consola.bat` ca să vezi eroarea; `test.bat` rulează testele automate.
- Repornire de la zero: șterge folderul `data\` (pierzi istoricul → pot apărea dubluri!).

## Cum postează (și riscuri)

Postarea se face prin **site-ul TikTok Studio** (ca și cum ai da tu click), cu Chrome-ul în care te-ai logat. TikTok poate cere uneori
captcha/verificare (o rezolvi tu în fereastra Chrome care se deschide) și poate da **shadowban** dacă postezi prea mult.
Implicit: presetarea **Normal** (45/zi, 20–30 min + variație aleatoare ±4 min, o postare odată). Dacă TikTok arată un avertisment,
programul se oprește singur din postat (1h → 2h → 4h…) și îți arată mesajul. Dacă aplicația se închide în timpul unei postări,
la repornire verifică întâi pe TikTok dacă clipul a apărut, ca să nu-l posteze de două ori.

**Alternativa oficială:** TikTok Content Posting API (developers.tiktok.com) – trebuie aplicație de developer + audit; până la
audit aplicațiile pot posta doar **privat (SELF_ONLY)**. De aceea programul folosește site-ul.

## Pentru dezvoltator

- `Zbir Cliping.exe` = lansator mic (C, cu iconiță) care pornește `.venv\Scripts\pythonw.exe -m zbirclip app`. Se reconstruiește cu `build_exe.bat` (Windows) sau `launcher/build_launcher.sh` (Linux, zig) sau GitHub Actions (`.github/workflows/build-windows.yml`).
- Fără fereastră: `python -m zbirclip run` (aplicația web la http://127.0.0.1:8777). Teste: `python -m zbirclip selftest`.
- Cod: `zbirclip/app.py` (orchestrare 24/7), `youtube.py` (live/DVR/VOD/chat), `recorder.py`, `transcribe.py`, `detect.py`, `llm.py`, `layout.py`+`facecam.py`, `render.py`, `tiktok.py`, `web.py`+`web/index.html`, `db.py`.
- Cod și idei preluate: vezi `THIRD_PARTY.md`.
- Securitate: serverul ascultă doar pe 127.0.0.1, cererile care modifică ceva cer tokenul sesiunii (`X-Zbir-Token`), fișierele servite sunt limitate la folderul de date, ffmpeg e pornit fără shell, update-ul vine doar din repo-ul fix, prin HTTPS. Starea e în SQLite (WAL) cu backup orar și recuperare automată dacă baza e coruptă.

## Limitări cunoscute (sincer)

- Postarea folosește site-ul TikTok, nu un API oficial: dacă TikTok schimbă pagina de upload, postarea poate eșua până la o actualizare (motivul + poze în `data\screens\`). Selectorii au fost testați doar pe o pagină de test, nu pe TikTok real.
- Contul TikTok trebuie logat **o dată pe PC-ul tău** (butonul Login TikTok) – logarea de pe alt calculator nu se transferă.
- Partea deja transmisă a unui live (DVR) depinde de YouTube; dacă YouTube refuză, programul reîncearcă singur și, după ce live-ul se termină, ia restul din VOD. Implicit ia maxim ultimele 12h din urmă (setarea `backfill_max_hours` din `config.yaml`); cât de mult în urmă permite YouTube nu e garantat.
- Detecția de râs/momente amuzante e pe reguli (volum, țipete, cuvinte, chat); cu un LLM pus în `.env` alegerea și titlurile sunt mai bune. Verifică clipurile înainte de „Postează”.
- Whisper mai greșește cuvinte când e zgomot / muzică / vorbesc doi deodată.
- Avertismentele TikTok sunt detectate după textul din pagină (EN/RO); formulările exacte ale TikTok nu au putut fi verificate fără să postăm de pe alt calculator.
- „Stai așa…” (wait for it) e un text-teaser, nu un freeze-frame real. Râsul e detectat pe reguli, nu cu un model ML.
