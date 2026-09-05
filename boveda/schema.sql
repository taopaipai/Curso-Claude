-- Boveda de contenido: esquema SQLite
-- Cada etapa del pipeline es idempotente y reanudable: el estado vive en `items.estado`.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Un "origen" es un lote de guardados importado: una carpeta de IG,
-- una playlist de YouTube, el export de TikTok, una lista suelta de URLs.
CREATE TABLE IF NOT EXISTS origenes (
    id            INTEGER PRIMARY KEY,
    plataforma    TEXT NOT NULL,              -- instagram | tiktok | youtube | web
    carpeta       TEXT,                       -- nombre de la coleccion/carpeta de guardados
    descripcion   TEXT,
    importado_en  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (plataforma, carpeta)
);

-- El guardado en si. `url_canonica` es la clave de deduplicacion entre importaciones.
CREATE TABLE IF NOT EXISTS items (
    id             INTEGER PRIMARY KEY,
    origen_id      INTEGER REFERENCES origenes(id) ON DELETE SET NULL,
    plataforma     TEXT NOT NULL,
    url_canonica   TEXT NOT NULL UNIQUE,
    id_externo     TEXT,                      -- id del video/post en la plataforma
    autor          TEXT,
    titulo         TEXT,
    descripcion    TEXT,
    duracion_seg   INTEGER,
    publicado_en   TEXT,
    guardado_en    TEXT,                      -- cuando lo guardaste tu, si el export lo trae
    idioma         TEXT,
    metricas_json  TEXT,                      -- todo lo que reporta la plataforma, en crudo
    publicado_ts   INTEGER,                   -- epoch de publicacion, para fechar los comentarios
    vistas         INTEGER,
    likes          INTEGER,
    comentarios_n  INTEGER,
    compartidos    INTEGER,
    guardados      INTEGER,                   -- solo TikTok lo publica
    crudo_json     TEXT,                      -- el registro original del export, sin tocar
    ruta_media     TEXT,
    ruta_audio     TEXT,
    estado         TEXT NOT NULL DEFAULT 'importado',
                   -- importado -> descargado -> transcrito -> analizado
    error          TEXT,
    creado_en      TEXT NOT NULL DEFAULT (datetime('now')),
    actualizado_en TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_items_estado     ON items(estado);
CREATE INDEX IF NOT EXISTS idx_items_plataforma ON items(plataforma);

CREATE TABLE IF NOT EXISTS transcripciones (
    id             INTEGER PRIMARY KEY,
    item_id        INTEGER NOT NULL UNIQUE REFERENCES items(id) ON DELETE CASCADE,
    motor          TEXT NOT NULL,
    idioma         TEXT,
    texto          TEXT NOT NULL,
    segmentos_json TEXT,                      -- [{inicio, fin, texto}, ...] para citar con timestamp
    creado_en      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Los comentarios mas votados de cada publicacion. Son la reaccion real del
-- publico: que parte del video comento la gente, que objeto, que pidio. Sirven
-- tanto para el analisis como para saber que responder cuando publiquemos lo
-- nuestro.
CREATE TABLE IF NOT EXISTS comentarios (
    id             INTEGER PRIMARY KEY,
    item_id        INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    id_externo     TEXT,
    posicion       INTEGER NOT NULL,          -- 1 = el mas votado
    autor          TEXT,
    es_del_autor   INTEGER NOT NULL DEFAULT 0,
    texto          TEXT NOT NULL,
    likes          INTEGER,
    publicado_ts   INTEGER,
    segundos_tras  INTEGER,                   -- cuanto despues del video se escribio
    tiempo_exacto  INTEGER NOT NULL DEFAULT 0, -- 0 = estimado a partir de "hace 3 meses"
    capturado_en   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (item_id, posicion)
);

CREATE INDEX IF NOT EXISTS idx_comentarios_item ON comentarios(item_id);

-- Instantanea de las metricas cada vez que se consulta la publicacion. Un
-- guardado de 2019 vale precisamente por como se movia entonces; si solo
-- guardamos el ultimo numero, esa historia se pierde.
CREATE TABLE IF NOT EXISTS metricas (
    id           INTEGER PRIMARY KEY,
    item_id      INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    capturado_en TEXT NOT NULL DEFAULT (datetime('now')),
    vistas       INTEGER,
    likes        INTEGER,
    comentarios  INTEGER,
    compartidos  INTEGER,
    guardados    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_metricas_item ON metricas(item_id, capturado_en);

-- Texto quemado en pantalla, leido de los fotogramas. Para los videos de puro
-- texto (carruseles animados, reels sin voz) esto ES el contenido: sin esto el
-- item no tiene nada que analizar.
CREATE TABLE IF NOT EXISTS ocr (
    id        INTEGER PRIMARY KEY,
    item_id   INTEGER NOT NULL UNIQUE REFERENCES items(id) ON DELETE CASCADE,
    motor     TEXT NOT NULL,                -- claude:<modelo> | tesseract
    texto     TEXT NOT NULL,                -- texto unificado, en orden de aparicion
    n_fotogramas INTEGER NOT NULL DEFAULT 0,
    creado_en TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Un fotograma clave y lo que se leyo en el. Se guarda por separado para poder
-- citar "esto aparece en el segundo 7" al reconstruir la estructura.
CREATE TABLE IF NOT EXISTS fotogramas (
    id          INTEGER PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    indice      INTEGER NOT NULL,
    segundo     REAL,
    ruta        TEXT,
    texto       TEXT,
    descripcion TEXT,
    UNIQUE (item_id, indice)
);

-- El analisis estructurado que devuelve Claude, guardado entero mas
-- las columnas que mas se filtran, desnormalizadas para consultar rapido.
CREATE TABLE IF NOT EXISTS analisis (
    id                 INTEGER PRIMARY KEY,
    item_id            INTEGER NOT NULL UNIQUE REFERENCES items(id) ON DELETE CASCADE,
    modelo             TEXT NOT NULL,
    tipo_contenido     TEXT,                  -- viral | instructivo | noticia | opinion | promocional | otro
    nicho              TEXT,
    tema_principal     TEXT,
    hook_texto         TEXT,
    hook_tecnica       TEXT,
    vigencia_estado    TEXT,                  -- vigente | caducado | atemporal
    valor_historico    INTEGER,               -- 1-5: cuanto vale guardarlo aunque este caducado
    analisis_json      TEXT NOT NULL,         -- el objeto completo
    creado_en          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_analisis_tipo     ON analisis(tipo_contenido);
CREATE INDEX IF NOT EXISTS idx_analisis_nicho    ON analisis(nicho);
CREATE INDEX IF NOT EXISTS idx_analisis_vigencia ON analisis(vigencia_estado);

-- Contenido nuevo derivado de un item: guion de reel, carrusel, hilo, newsletter.
CREATE TABLE IF NOT EXISTS producciones (
    id          INTEGER PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    formato     TEXT NOT NULL,                -- reel | carrusel | hilo | newsletter | short
    nicho       TEXT,
    titulo      TEXT,
    cuerpo      TEXT NOT NULL,
    modelo      TEXT,
    estado      TEXT NOT NULL DEFAULT 'borrador',  -- borrador | aprobado | publicado
    creado_en   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_producciones_item ON producciones(item_id);

-- El video montado a partir de un guion: voz sintetizada, subtitulos quemados y
-- fondo. Se guarda el desglose en escenas para poder re-montar cambiando la voz
-- o el fondo sin volver a pedirle el desglose al modelo.
CREATE TABLE IF NOT EXISTS montajes (
    id            INTEGER PRIMARY KEY,
    produccion_id INTEGER NOT NULL UNIQUE REFERENCES producciones(id) ON DELETE CASCADE,
    ruta_video    TEXT NOT NULL,
    ruta_audio    TEXT,
    ruta_subtitulos TEXT,
    duracion_seg  REAL,
    voz           TEXT,
    escenas_json  TEXT NOT NULL,
    creado_en     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Cada intento de publicar una produccion en una red. Es el registro de lo que
-- de verdad salio: nada se publica sin una fila aqui, y nada se publica dos
-- veces (indice unico sobre lo ya publicado).
CREATE TABLE IF NOT EXISTS publicaciones (
    id             INTEGER PRIMARY KEY,
    produccion_id  INTEGER NOT NULL REFERENCES producciones(id) ON DELETE CASCADE,
    red            TEXT NOT NULL,            -- instagram | tiktok | youtube | x | archivo
    estado         TEXT NOT NULL DEFAULT 'programada',
                   -- programada -> publicada | error | cancelada
    programado_para TEXT,                    -- ISO-8601; NULL = en cuanto se procese la cola
    publicado_en   TEXT,
    media_ruta     TEXT,                     -- archivo local que se sube
    media_url      TEXT,                     -- URL publica, para las redes que la exigen
    id_remoto      TEXT,
    url_remota     TEXT,
    intentos       INTEGER NOT NULL DEFAULT 0,
    error          TEXT,
    creado_en      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pub_estado ON publicaciones(estado, programado_para);

-- Una produccion no se publica dos veces en la misma red.
CREATE UNIQUE INDEX IF NOT EXISTS idx_pub_unica
    ON publicaciones(produccion_id, red) WHERE estado = 'publicada';

CREATE TABLE IF NOT EXISTS etiquetas (
    item_id   INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    etiqueta  TEXT NOT NULL,
    PRIMARY KEY (item_id, etiqueta)
);

-- Busqueda full-text sobre lo que de verdad se busca: titulo, transcripcion y analisis.
CREATE VIRTUAL TABLE IF NOT EXISTS busqueda USING fts5(
    titulo, autor, transcripcion, analisis,
    item_id UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);
