# Search tuning: frontend-first scoring

Branch: `main` — Delivery: `ask-on-risk` — Runner: `cd backend && .venv/bin/python -m pytest -q`

Re-weights the deterministic scorer and the search terms so the ranking matches the
profile in `writing-block.md`: a junior frontend developer in Quilmes/AMBA, remote
Argentina first, backend only as a complement.

## Specs

- **S1 — Rol por encima del stack.** "Mi fortaleza principal es el frontend" y "No me
  presentes como Backend Developer ni como Full Stack con dominio equilibrado". El score
  debe tener una dimensión de rol: frontend > mobile > fullstack > desconocido > backend.
  Un puesto backend puro no puede notificar aunque nombre Node.js o TypeScript.
- **S2 — Nivel real, no palabras del título.** "No recomiendes puestos que sean en
  realidad Semi-Senior/Mid-Level aunque incluyan Junior o React en el título." Un título
  con marcador senior (`SENIOR/SSR`, `Sr.`, `Lead`) es senior, no mid. Un mid no alcanza
  el umbral de notificación.
- **S3 — Experiencia hasta 2 años.** "Puedes incluir ofertas que pidan hasta 2 años de
  experiencia... Si piden 3 años o más de experiencia profesional... normalmente
  descártalas." La curva debe castigar fuerte a partir de 3 años.
- **S4 — Ubicación Quilmes/AMBA.** "Remoto dentro de Argentina" primero, "Remoto para
  Latinoamérica" segundo, "Híbrido o presencial en Quilmes y otras zonas razonablemente
  accesibles del AMBA, incluida CABA" tercero, y "Remoto internacional solo cuando la
  empresa pueda contratar personas residentes en Argentina". Presencial AMBA > presencial
  en otra provincia; remoto con marcador extranjero queda por debajo del remoto AR/LatAm.
- **S5 — Stack frontend primero.** "JavaScript y TypeScript", "React", "Next.js y Vite",
  "React Native con Expo", "HTML y CSS", "Tailwind CSS y DaisyUI" son el área principal;
  "Node.js y Express", "MongoDB y PostgreSQL", "Supabase", "Prisma" son "conocimientos
  complementarios" y pesan mucho menos.
- **S6 — Búsquedas §8.** "Combina búsquedas en español e inglés": frontend react junior,
  desarrollador frontend, react junior, pasantía, y "No concentres la búsqueda en Backend
  Node.js."
- **S7 — Banderas que la oferta debe exponer.** "Identifica si piden experiencia
  profesional comprobable, estudios universitarios, inglés, residencia en un país
  concreto" y "señala claramente las pasantías no remuneradas". Se guardan `english_level`
  / `english_required`, `unpaid` y `degree_required` en el breakdown; inglés y pasantía no
  remunerada se muestran en la tarjeta.
- **S8 — Nada inventado.** "No inventes salarios, requisitos, tecnologías, beneficios,
  fechas ni datos de contacto." El scorer no puntúa inglés (el perfil prohíbe presuponer
  el nivel); solo lo reporta.
- **S9 — Umbral 80 intacto.** El umbral de notificación sigue en 80 hasta que la
  calibración contra las filas reales demuestre lo contrario.

## Tasks

| ID | Specs | Route | Status |
| --- | --- | --- | --- |
| T1 | S1,S2,S3,S4,S5,S7 | inline | done — `scoring.py` reescrito, 80 tests verdes |
| T2 | S6 | inline | done — 9 términos LinkedIn + 3 de Indeed |
| T3 | S1,S5,S7 | inline | done — 6 medidores, chips `inglés` / `no remunerada` |
| T4 | S9 | inline | done — 20 filas re-scoreadas, ranking verificado |
| T5 | all | inline | done — `AGENTS.md` actualizado |

## Log

**L1 (usuario, 2026-11-12):** "ok, vamos a afinar la busqueda. toma este prompt para
saber lo que quiero." Adjunta `writing-block.md` (perfil completo, 178 líneas).

**L2 (medición):** sobre las 20 filas reales, el mejor score era un
"FullStack Developer SENIOR/SSR Angular/NodeJS" con 73, y un "Desarrollador Frontend
(UX/UI)" de Buenos Aires quedaba en 43. El scorer no tenía dimensión de rol y clasificaba
"SENIOR/SSR" como `mid` porque `ssr` estaba en los marcadores de mid.

**L3 (implementación):** el nuevo reparto es role 15 / stack 30 / seniority 25 /
experience 10 / location 10 / recency 10. Se separaron los marcadores senior de título
(bare `lead`, `staff`, `principal`) de los de descripción (frases como `senior level`,
`tech lead`), porque "lead the activities of a team" marcaba senior una fila frontend
real. `all levels` cuenta como junior-accepting.

**L4 (verificación, 2026-11-12):** 284 tests verdes (80 de scoring), `astro check` 0,
build de una página. Recalculadas las 20 filas reales: la inversión desapareció
(Angular senior 73 → 52, frontend 43 → 54), máximo 68, ninguna notificable porque ese
feed no trae ningún junior frontend React en Argentina. Techo teórico del modelo: 81
para una tarjeta LinkedIn sin descripción con título frontend junior React, 100 con JD.
