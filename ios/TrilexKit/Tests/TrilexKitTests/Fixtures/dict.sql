BEGIN TRANSACTION;
CREATE TABLE audio(
  lang    TEXT NOT NULL,
  norm    TEXT NOT NULL,
  word    TEXT NOT NULL,
  data    BLOB NOT NULL,
  bytes   INTEGER,
  fname   TEXT,
  accent  TEXT,
  PRIMARY KEY(lang, norm)
) WITHOUT ROWID;
INSERT INTO "audio" VALUES('en','water','water',X'494433666978747572652D6D7033',14,'En-us-water.ogg','US');
INSERT INTO "audio" VALUES('sv','vatten','vatten',X'49443376617474656E',9,'Sv-vatten.ogg',NULL);
CREATE TABLE audio_ref(
  lang  TEXT NOT NULL,
  norm  TEXT NOT NULL,
  word  TEXT NOT NULL,
  url   TEXT NOT NULL,
  fname TEXT NOT NULL,
  tags  TEXT,
  pref  INTEGER NOT NULL DEFAULT 5,   -- lower = preferred accent
  PRIMARY KEY(lang, norm, fname)
) WITHOUT ROWID;
CREATE TABLE entry(
  id     INTEGER PRIMARY KEY,
  lang   TEXT NOT NULL,          -- en | sv | zh
  word   TEXT NOT NULL,          -- display headword (zh: simplified)
  norm   TEXT NOT NULL,          -- casefolded/stripped key for lookup
  pos    TEXT,
  extra  TEXT,                   -- JSON: pinyin, traditional, inflections...
  src    TEXT NOT NULL
);
INSERT INTO "entry" VALUES(1,'sv','vatten','vatten','nn','{"phonetic": "vat:en"}','folkets');
INSERT INTO "entry" VALUES(2,'en','water','water','nn',NULL,'folkets');
INSERT INTO "entry" VALUES(3,'en','water','water','vb',NULL,'folkets');
INSERT INTO "entry" VALUES(4,'zh','水','水',NULL,'{"pinyin": "shuǐ", "trad": "水"}','cedict');
INSERT INTO "entry" VALUES(5,'zh','水饺','水饺',NULL,'{"pinyin": "shuǐ jiǎo", "trad": "水餃"}','cedict');
INSERT INTO "entry" VALUES(6,'sv','hus','hus','nn',NULL,'folkets');
INSERT INTO "entry" VALUES(7,'en','house','house','nn',NULL,'folkets');
INSERT INTO "entry" VALUES(8,'sv','byggnad','byggnad','nn',NULL,'folkets');
INSERT INTO "entry" VALUES(9,'en','run','run','vb',NULL,'folkets');
INSERT INTO "entry" VALUES(10,'sv','springa','springa','vb',NULL,'folkets');
INSERT INTO "entry" VALUES(11,'sv','ran','ran','nn',NULL,'folkets');
INSERT INTO "entry" VALUES(12,'zh','然','然',NULL,'{"pinyin": "rán"}','cedict');
INSERT INTO "entry" VALUES(13,'zh','房子','房子',NULL,'{"pinyin": "fáng zi"}','cedict');
INSERT INTO "entry" VALUES(14,'en','beautiful','beautiful','jj',NULL,'folkets');
INSERT INTO "entry" VALUES(15,'sv','vacker','vacker','jj',NULL,'folkets');
INSERT INTO "entry" VALUES(16,'zh','美丽','美丽',NULL,'{"pinyin": "měi lì", "trad": "美麗"}','cedict');
INSERT INTO "entry" VALUES(17,'sv','skön','skön','jj',NULL,'folkets');
INSERT INTO "entry" VALUES(18,'en','Water','water','pm',NULL,'folkets');
CREATE TABLE etym(
  id  INTEGER PRIMARY KEY,
  sha TEXT UNIQUE,
  txt TEXT NOT NULL
);
INSERT INTO "etym" VALUES(1,'sha1','From Middle English water, from Old English wæter, from Proto-Germanic *watōr.');
INSERT INTO "etym" VALUES(2,'sha2','A surname.');
INSERT INTO "etym" VALUES(3,'sha3','From Old Swedish vatn, from Old Norse vatn.');
INSERT INTO "etym" VALUES(4,'sha4','Pictogram of flowing water.');
INSERT INTO "etym" VALUES(5,'sha5','From Ancient Greek θέρμη (thérmē, “heat”) + dynamics.');
INSERT INTO "etym" VALUES(6,'sha6','From Old English hūs.');
INSERT INTO "etym" VALUES(7,'sha7','From Old English rinnan.');
CREATE TABLE example(
  lang       TEXT NOT NULL,     -- language the sentence is written in
  norm       TEXT NOT NULL,     -- lemma it illustrates
  text       TEXT NOT NULL,
  trans      TEXT,              -- translation, if the source had one
  trans_lang TEXT,
  src        TEXT NOT NULL,
  score      REAL NOT NULL DEFAULT 0   -- lower sorts first
);
INSERT INTO "example" VALUES('en','water','I drink water every day.','Jag dricker vatten varje dag.','sv','tatoeba',0.5);
INSERT INTO "example" VALUES('en','water','Water is wet.',NULL,NULL,'wikt-sense',0.3);
INSERT INTO "example" VALUES('en','water','Could I have some water?','我可以喝点水吗？','zh','tatoeba',0.5);
INSERT INTO "example" VALUES('en','water','I drink water every day.','Jag dricker vatten varje dag.','sv','tatoeba',0.6);
INSERT INTO "example" VALUES('zh','水','我要喝水。','I want to drink water.','en','tatoeba',0.5);
INSERT INTO "example" VALUES('sv','hus','Huset är stort.','The house is big.','en','folkets',0.0);
INSERT INTO "example" VALUES('sv','hus','Husen är röda och husets tak är svart.','The houses are red.','en','tatoeba',0.2);
INSERT INTO "example" VALUES('en','run','He ran home.','Han sprang hem.','sv','tatoeba',0.5);
INSERT INTO "example" VALUES('en','run','Running is fun; I run daily.',NULL,NULL,'wikt-quote',2.0);
CREATE TABLE form(
  norm     TEXT NOT NULL,
  lang     TEXT NOT NULL,
  entry_id INTEGER NOT NULL REFERENCES entry(id) ON DELETE CASCADE,
  kind     TEXT NOT NULL,       -- head | pinyin | trad | infl | syn
  disp     TEXT NOT NULL        -- what to show: a synonym form shows itself,
                                -- a pinyin form shows its Chinese characters
);
INSERT INTO "form" VALUES('vatten','sv',1,'head','vatten');
INSERT INTO "form" VALUES('water','en',2,'head','water');
INSERT INTO "form" VALUES('water','en',3,'head','water');
INSERT INTO "form" VALUES('水','zh',4,'head','水');
INSERT INTO "form" VALUES('shui','zh',4,'pinyin','水');
INSERT INTO "form" VALUES('水饺','zh',5,'head','水饺');
INSERT INTO "form" VALUES('shuijiao','zh',5,'pinyin','水饺');
INSERT INTO "form" VALUES('水餃','zh',5,'trad','水饺');
INSERT INTO "form" VALUES('hus','sv',6,'head','hus');
INSERT INTO "form" VALUES('house','en',7,'head','house');
INSERT INTO "form" VALUES('byggnad','sv',8,'head','byggnad');
INSERT INTO "form" VALUES('run','en',9,'head','run');
INSERT INTO "form" VALUES('springa','sv',10,'head','springa');
INSERT INTO "form" VALUES('ran','sv',11,'head','ran');
INSERT INTO "form" VALUES('然','zh',12,'head','然');
INSERT INTO "form" VALUES('ran','zh',12,'pinyin','然');
INSERT INTO "form" VALUES('房子','zh',13,'head','房子');
INSERT INTO "form" VALUES('fangzi','zh',13,'pinyin','房子');
INSERT INTO "form" VALUES('beautiful','en',14,'head','beautiful');
INSERT INTO "form" VALUES('vacker','sv',15,'head','vacker');
INSERT INTO "form" VALUES('美丽','zh',16,'head','美丽');
INSERT INTO "form" VALUES('meili','zh',16,'pinyin','美丽');
INSERT INTO "form" VALUES('美麗','zh',16,'trad','美丽');
INSERT INTO "form" VALUES('skön','sv',17,'head','skön');
INSERT INTO "form" VALUES('water','en',18,'head','Water');
INSERT INTO "form" VALUES('hus','sv',8,'syn','hus');
CREATE TABLE image(
  lang    TEXT NOT NULL,
  norm    TEXT NOT NULL,
  word    TEXT NOT NULL,
  data    BLOB NOT NULL,
  w       INTEGER, h INTEGER,
  license TEXT, artist TEXT, file TEXT, page TEXT,
  PRIMARY KEY(lang, norm)
) WITHOUT ROWID;
INSERT INTO "image" VALUES('en','house','house',X'89504E47686F757365',64,64,'CC0',NULL,'House.jpg',NULL);
INSERT INTO "image" VALUES('en','water','water',X'89504E470D0A1A0A66697874757265',80,60,'CC BY-SA 4.0','A. Photographer','Water.jpg','https://commons.wikimedia.org/wiki/File:Water.jpg');
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE pivot(
  entry_id INTEGER NOT NULL REFERENCES entry(id) ON DELETE CASCADE,
  en_norm  TEXT NOT NULL,
  weight   REAL NOT NULL DEFAULT 1.0
);
INSERT INTO "pivot" VALUES(1,'water',1.0);
INSERT INTO "pivot" VALUES(2,'water',1.0);
INSERT INTO "pivot" VALUES(3,'water',0.7);
INSERT INTO "pivot" VALUES(4,'water',1.0);
INSERT INTO "pivot" VALUES(4,'river',0.5);
INSERT INTO "pivot" VALUES(5,'dumpling',1.0);
INSERT INTO "pivot" VALUES(6,'house',1.0);
INSERT INTO "pivot" VALUES(7,'house',1.0);
INSERT INTO "pivot" VALUES(8,'building',1.0);
INSERT INTO "pivot" VALUES(8,'house',0.6);
INSERT INTO "pivot" VALUES(9,'run',1.0);
INSERT INTO "pivot" VALUES(10,'run',1.0);
INSERT INTO "pivot" VALUES(11,'robbery',1.0);
INSERT INTO "pivot" VALUES(12,'correct',0.8);
INSERT INTO "pivot" VALUES(12,'so',0.4);
INSERT INTO "pivot" VALUES(13,'house',1.0);
INSERT INTO "pivot" VALUES(13,'building',0.5);
INSERT INTO "pivot" VALUES(14,'beautiful',1.0);
INSERT INTO "pivot" VALUES(15,'beautiful',1.0);
INSERT INTO "pivot" VALUES(15,'pretty',0.6);
INSERT INTO "pivot" VALUES(16,'beautiful',1.0);
INSERT INTO "pivot" VALUES(17,'beautiful',0.8);
INSERT INTO "pivot" VALUES(17,'comfortable',1.0);
CREATE TABLE pivot_df(en_norm TEXT PRIMARY KEY, df INTEGER NOT NULL) WITHOUT ROWID;
INSERT INTO "pivot_df" VALUES('beautiful',4);
INSERT INTO "pivot_df" VALUES('building',2);
INSERT INTO "pivot_df" VALUES('comfortable',1);
INSERT INTO "pivot_df" VALUES('correct',1);
INSERT INTO "pivot_df" VALUES('dumpling',1);
INSERT INTO "pivot_df" VALUES('house',4);
INSERT INTO "pivot_df" VALUES('pretty',1);
INSERT INTO "pivot_df" VALUES('river',1);
INSERT INTO "pivot_df" VALUES('robbery',1);
INSERT INTO "pivot_df" VALUES('run',2);
INSERT INTO "pivot_df" VALUES('so',1);
INSERT INTO "pivot_df" VALUES('water',4);
CREATE TABLE sense(
  id       INTEGER PRIMARY KEY,
  entry_id INTEGER NOT NULL REFERENCES entry(id) ON DELETE CASCADE,
  lang     TEXT NOT NULL,        -- language the gloss is written in
  gloss    TEXT NOT NULL,
  ord      INTEGER DEFAULT 0
);
INSERT INTO "sense" VALUES(1,1,'en','water',0);
INSERT INTO "sense" VALUES(2,2,'sv','vatten',0);
INSERT INTO "sense" VALUES(3,3,'sv','vattna',0);
INSERT INTO "sense" VALUES(4,4,'en','water',0);
INSERT INTO "sense" VALUES(5,4,'en','river',1);
INSERT INTO "sense" VALUES(6,5,'en','dumpling',0);
INSERT INTO "sense" VALUES(7,6,'en','house',0);
INSERT INTO "sense" VALUES(8,7,'sv','hus',0);
INSERT INTO "sense" VALUES(9,8,'en','building',0);
INSERT INTO "sense" VALUES(10,8,'en','house',1);
INSERT INTO "sense" VALUES(11,9,'sv','springa',0);
INSERT INTO "sense" VALUES(12,10,'en','run',0);
INSERT INTO "sense" VALUES(13,10,'sv','röra sig snabbt',1);
INSERT INTO "sense" VALUES(14,11,'en','robbery',0);
INSERT INTO "sense" VALUES(15,12,'en','correct',0);
INSERT INTO "sense" VALUES(16,12,'en','so',1);
INSERT INTO "sense" VALUES(17,13,'en','house',0);
INSERT INTO "sense" VALUES(18,13,'en','building',1);
INSERT INTO "sense" VALUES(19,14,'sv','vacker',0);
INSERT INTO "sense" VALUES(20,14,'sv','skön',1);
INSERT INTO "sense" VALUES(21,15,'en','beautiful',0);
INSERT INTO "sense" VALUES(22,15,'en','pretty',1);
INSERT INTO "sense" VALUES(23,16,'en','beautiful',0);
INSERT INTO "sense" VALUES(24,17,'en','beautiful',0);
INSERT INTO "sense" VALUES(25,17,'en','comfortable',1);
INSERT INTO "sense" VALUES(26,18,'sv','Water (efternamn)',0);
CREATE TABLE vocab(
  norm  TEXT NOT NULL,
  lang  TEXT NOT NULL,
  disp  TEXT NOT NULL,
  score REAL NOT NULL,
  core  INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(norm, lang)
) WITHOUT ROWID;
INSERT INTO "vocab" VALUES('beautiful','en','beautiful',0.6667,1);
INSERT INTO "vocab" VALUES('byggnad','sv','byggnad',1.1111,1);
INSERT INTO "vocab" VALUES('fangzi','zh','房子',0.7143,1);
INSERT INTO "vocab" VALUES('house','en','house',1.25,1);
INSERT INTO "vocab" VALUES('hus','sv','hus',1.4286,1);
INSERT INTO "vocab" VALUES('meili','zh','美丽',0.5882,1);
INSERT INTO "vocab" VALUES('ran','sv','ran',0.8333,1);
INSERT INTO "vocab" VALUES('ran','zh','然',0.7692,1);
INSERT INTO "vocab" VALUES('run','en','run',1.0,1);
INSERT INTO "vocab" VALUES('shui','zh','水',2.0,1);
INSERT INTO "vocab" VALUES('shuijiao','zh','水饺',1.6667,1);
INSERT INTO "vocab" VALUES('skön','sv','skön',0.5556,1);
INSERT INTO "vocab" VALUES('springa','sv','springa',0.9091,1);
INSERT INTO "vocab" VALUES('thermodynamics','en','thermodynamics',0.1,0);
INSERT INTO "vocab" VALUES('vacker','sv','vacker',0.625,1);
INSERT INTO "vocab" VALUES('vatten','sv','vatten',5.0,1);
INSERT INTO "vocab" VALUES('water','en','water',3.3333,1);
INSERT INTO "vocab" VALUES('房子','zh','房子',0.7143,1);
INSERT INTO "vocab" VALUES('水','zh','水',2.0,1);
INSERT INTO "vocab" VALUES('水餃','zh','水饺',1.6667,1);
INSERT INTO "vocab" VALUES('水饺','zh','水饺',1.6667,1);
INSERT INTO "vocab" VALUES('然','zh','然',0.7692,1);
INSERT INTO "vocab" VALUES('美丽','zh','美丽',0.5882,1);
INSERT INTO "vocab" VALUES('美麗','zh','美丽',0.5882,1);
CREATE TABLE wform(
  lang       TEXT NOT NULL,
  lemma      TEXT NOT NULL,
  lemma_norm TEXT NOT NULL,
  pos        TEXT,
  form       TEXT NOT NULL,
  norm       TEXT NOT NULL,
  tags       TEXT NOT NULL
);
INSERT INTO "wform" VALUES('en','run','run','verb','ran','ran','past');
INSERT INTO "wform" VALUES('en','run','run','verb','running','running','present participle');
INSERT INTO "wform" VALUES('en','run','run','verb','runs','runs','present singular third-person');
INSERT INTO "wform" VALUES('en','run','run','verb','run','run','past participle');
INSERT INTO "wform" VALUES('en','run','run','noun','runs','runs','plural');
INSERT INTO "wform" VALUES('sv','hus','hus','noun','huset','huset','definite singular');
INSERT INTO "wform" VALUES('sv','hus','hus','noun','husen','husen','definite plural');
INSERT INTO "wform" VALUES('sv','hus','hus','noun','hus','hus','indefinite plural');
INSERT INTO "wform" VALUES('sv','hus','hus','noun','husets','husets','genitive definite singular');
INSERT INTO "wform" VALUES('sv','springa','springa','verb','sprang','sprang','past');
INSERT INTO "wform" VALUES('sv','springa','springa','verb','sprungit','sprungit','supine');
INSERT INTO "wform" VALUES('sv','springa','springa','verb','ſpringa','springa','infinitive');
INSERT INTO "wform" VALUES('sv','springa','springa','verb','springer','springer','present');
INSERT INTO "wform" VALUES('en','water','water','noun','waters','waters','plural');
INSERT INTO "wform" VALUES('en','water','water','verb','watered','watered','past');
INSERT INTO "wform" VALUES('en','HU','hu','noun','hu','hu','plural');
INSERT INTO "wform" VALUES('en','beautiful','beautiful','adj','more beautiful','more beautiful','comparative');
CREATE TABLE wik(
  id      INTEGER PRIMARY KEY,
  lang    TEXT NOT NULL,
  word    TEXT NOT NULL,
  norm    TEXT NOT NULL,
  pos     TEXT,
  ipa     TEXT,
  etym_id INTEGER REFERENCES etym(id),
  gloss   TEXT
);
INSERT INTO "wik" VALUES(1,'en','water','water','noun','/ˈwɔːtə/',1,'A clear liquid | The liquid in rivers and lakes');
INSERT INTO "wik" VALUES(2,'en','Water','water','name',NULL,2,'A surname');
INSERT INTO "wik" VALUES(3,'en','water','water','verb','/ˈwɔːtə/',1,'To pour water on');
INSERT INTO "wik" VALUES(4,'sv','vatten','vatten','noun','/ˈvatːɛn/',3,'en genomskinlig vätska');
INSERT INTO "wik" VALUES(5,'zh','水','水','character',NULL,4,'water');
INSERT INTO "wik" VALUES(6,'en','thermodynamics','thermodynamics','noun','/ˌθɜːməʊdaɪˈnæmɪks/',5,'The science of heat and work');
INSERT INTO "wik" VALUES(7,'en','house','house','noun','/haʊs/',6,'A building for living in');
INSERT INTO "wik" VALUES(8,'en','run','run','verb','/ɹʌn/',7,'To move swiftly on foot');
INSERT INTO "wik" VALUES(9,'en','ran','ran','verb','/ɹæn/',NULL,'simple past of run');
INSERT INTO "wik" VALUES(10,'sv','hus','hus','noun','/hʉːs/',NULL,'byggnad att bo i');
CREATE INDEX ix_wik_lw    ON wik(lang, norm);
CREATE INDEX ix_entry_ln  ON entry(lang, norm);
CREATE INDEX ix_sense_e   ON sense(entry_id);
CREATE INDEX ix_pivot_en  ON pivot(en_norm);
CREATE INDEX ix_pivot_e   ON pivot(entry_id);
CREATE INDEX ix_form_n    ON form(norm);
CREATE INDEX ix_form_e    ON form(entry_id);
CREATE INDEX ix_wform_norm  ON wform(norm);
CREATE INDEX ix_wform_lemma ON wform(lang, lemma_norm);
COMMIT;
