# Cod și idei folosite (Third-party)

| proiect | licență | ce am folosit |
|---|---|---|
| [Bodila51/gtavi-claudecode](https://github.com/Bodila51/gtavi-claudecode) | MIT | baza: semnale de moment (loudness z-score, chat, hype words), subtitrare ASS cuvânt-cu-cuvânt (adaptată), `facecam.py` (detecția camerei, copiat și adaptat), chat tail YouTube |
| [wkaisertexas/tiktok-uploader](https://github.com/wkaisertexas/tiktok-uploader) | MIT | fluxul de upload prin browser (Playwright) și selectorii TikTok (`post_video_button`, editor contenteditable, confirmări) – reimplementat cu profil persistent |
| [utkualsir/sharkyclips](https://github.com/utkualsir/sharkyclips) | MIT | idei: detecție râs/strigăte, glosar de nume, clipurile cu probleme sunt ținute pentru verificare, scor viral de la LLM |
| [gummybearansh/Auto-Clipper](https://github.com/gummybearansh/Auto-Clipper) | – | idei: build-up înainte de vârf + payoff după, tăiere pe pauze naturale, layout facecam-sus / fallback blur |
| [jekku0966/HighlightMiner](https://github.com/jekku0966/HighlightMiner) | MIT | idee: fuziune semnale audio + Whisper + chat într-un singur scor, review uman înainte de export |
| [saifk6717/ClippingShorts](https://github.com/saifk6717/ClippingShorts) | vezi repo | idee: monitorizare live cu worker izolat per stream, judecător LLM peste semnalele gratuite |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Unlicense | descărcare live/DVR/VOD/chat (mecanismul „live from start” cu fragmente `sq`) |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | MIT | transcriere (large-v3, ro) |
| [Montserrat](https://github.com/JulietaUla/Montserrat) | OFL 1.1 (`fonts/OFL.txt`) | fontul subtitrărilor (diacritice ș ț ă â î) |
| [Anton, Bebas Neue, Archivo Black, Poppins, Bangers](https://fonts.google.com) | OFL 1.1 (`fonts/OFL-*.txt`) | fonturi alternative pentru subtitrări |
| [Twemoji (jdecked/twemoji)](https://github.com/jdecked/twemoji) | grafică CC-BY 4.0 (`assets/emoji/LICENSE-twemoji-graphics.txt`) | emoji-urile din clipuri (© Twitter/X și contribuitorii Twemoji) |
| sunete meme (`assets/sfx`) | create de noi (sintetizate în `make_sfx.py`) | boom / whoosh / pop – fără copyright |
| [YuNet – opencv_zoo](https://github.com/opencv/opencv_zoo) | MIT | modelul de detecție a feței (`models/`) |
| [Playwright](https://playwright.dev), [pywebview](https://pywebview.flowrl.com), [zig](https://ziglang.org) | Apache-2.0 / BSD / MIT | browser pentru postare, fereastra aplicației, compilarea lansatorului .exe |
