-- Ganttier: esquema SQLite (fuente de verdad del almacenamiento).
-- Fechas como TEXT ISO 'YYYY-MM-DD'. Booleanos como INTEGER 0/1. Listas como TEXT JSON.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS calendars (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  workweek TEXT NOT NULL DEFAULT '1111100',
  hours_per_day REAL NOT NULL DEFAULT 8,
  holidays_country TEXT DEFAULT 'CL',
  shift_pattern TEXT,
  shift_anchor TEXT,
  holidays_apply_to_shift INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS calendar_exceptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  calendar_id INTEGER NOT NULL REFERENCES calendars(id) ON DELETE CASCADE,
  date TEXT NOT NULL,
  working INTEGER NOT NULL DEFAULT 0,
  hours REAL NOT NULL DEFAULT 0,
  reason TEXT DEFAULT '',
  UNIQUE(calendar_id, date)
);

CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'proyecto' CHECK (mode IN ('proyecto','mantenimiento')),
  start_date TEXT NOT NULL,
  end_date TEXT,
  data_date TEXT,
  calendar_id INTEGER REFERENCES calendars(id),
  description TEXT DEFAULT '',
  currency TEXT DEFAULT 'CLP',
  active_baseline_id INTEGER,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS wbs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  parent_id INTEGER REFERENCES wbs(id) ON DELETE CASCADE,
  code TEXT NOT NULL,
  name TEXT NOT NULL,
  sort_order INTEGER DEFAULT 0,
  UNIQUE(project_id, code)
);

CREATE TABLE IF NOT EXISTS resources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,  -- NULL = global
  name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'trabajador',
  skills TEXT NOT NULL DEFAULT '[]',
  capacity_hours_day REAL NOT NULL DEFAULT 8,
  max_units REAL NOT NULL DEFAULT 1,
  calendar_id INTEGER REFERENCES calendars(id),
  cost_hour REAL DEFAULT 0,
  overtime_cost_hour REAL DEFAULT 0,
  max_overtime_hours_day REAL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  email TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS periodic_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  code TEXT NOT NULL,
  name TEXT NOT NULL,
  frequency TEXT NOT NULL DEFAULT 'M',
  interval INTEGER NOT NULL DEFAULT 1,
  duration_days INTEGER NOT NULL DEFAULT 1,
  work_hours REAL NOT NULL DEFAULT 8,
  crew_size REAL NOT NULL DEFAULT 1,
  required_skill TEXT DEFAULT '',
  resource_id INTEGER REFERENCES resources(id) ON DELETE SET NULL,
  first_due TEXT,
  preferred_months TEXT DEFAULT '',
  preferred_weekday INTEGER,
  day_of_month INTEGER,
  tol_before_days INTEGER DEFAULT 7,
  tol_after_days INTEGER DEFAULT 7,
  window_hard INTEGER DEFAULT 0,
  location TEXT DEFAULT '',
  equipment TEXT DEFAULT '',
  voltage_kv REAL,
  requires_outage INTEGER DEFAULT 0,
  discipline TEXT DEFAULT '',
  calendar_id INTEGER REFERENCES calendars(id),
  wbs_code TEXT DEFAULT '',
  priority INTEGER DEFAULT 50,
  cost_per_occurrence REAL DEFAULT 0,
  active INTEGER DEFAULT 1,
  horizon_end TEXT,
  UNIQUE(project_id, code)
);

CREATE TABLE IF NOT EXISTS activities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  wbs_id INTEGER REFERENCES wbs(id) ON DELETE SET NULL,
  code TEXT NOT NULL,
  name TEXT NOT NULL,
  duration_days INTEGER NOT NULL DEFAULT 1,
  work_hours REAL DEFAULT 0,
  cost_budget REAL DEFAULT 0,
  calendar_id INTEGER REFERENCES calendars(id),
  macro_month TEXT,
  window_start TEXT,
  window_end TEXT,
  window_hard INTEGER DEFAULT 0,
  constraint_type TEXT DEFAULT 'ASAP',
  constraint_date TEXT,
  priority INTEGER DEFAULT 50,
  planned_start TEXT,
  planned_finish TEXT,
  early_start TEXT,
  early_finish TEXT,
  late_start TEXT,
  late_finish TEXT,
  total_float INTEGER,
  free_float INTEGER,
  critical INTEGER DEFAULT 0,
  actual_start TEXT,
  actual_finish TEXT,
  percent_complete REAL DEFAULT 0,
  remaining_duration INTEGER,
  actual_hours REAL DEFAULT 0,
  actual_cost REAL DEFAULT 0,
  status TEXT DEFAULT 'no_iniciada',
  periodic_task_id INTEGER REFERENCES periodic_tasks(id) ON DELETE CASCADE,
  occurrence_index INTEGER,
  due_date TEXT,
  location TEXT DEFAULT '',
  equipment TEXT DEFAULT '',
  voltage_kv REAL,
  requires_outage INTEGER DEFAULT 0,
  discipline TEXT DEFAULT '',
  required_skill TEXT DEFAULT '',
  crew_size REAL DEFAULT 1,
  notes TEXT DEFAULT '',
  locked INTEGER DEFAULT 0,
  UNIQUE(project_id, code)
);
CREATE INDEX IF NOT EXISTS ix_act_project ON activities(project_id);
CREATE INDEX IF NOT EXISTS ix_act_start ON activities(project_id, planned_start);
CREATE INDEX IF NOT EXISTS ix_act_periodic ON activities(periodic_task_id);
CREATE INDEX IF NOT EXISTS ix_act_wbs ON activities(wbs_id);

CREATE TABLE IF NOT EXISTS relations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  pred_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
  succ_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
  type TEXT NOT NULL DEFAULT 'FS' CHECK (type IN ('FS','SS','FF','SF')),
  lag_days INTEGER NOT NULL DEFAULT 0,
  UNIQUE(pred_id, succ_id)
);
CREATE INDEX IF NOT EXISTS ix_rel_project ON relations(project_id);

CREATE TABLE IF NOT EXISTS assignments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
  resource_id INTEGER REFERENCES resources(id) ON DELETE CASCADE,
  skill TEXT DEFAULT '',
  units REAL NOT NULL DEFAULT 1,
  work_hours REAL NOT NULL DEFAULT 0,
  actual_hours REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_asg_activity ON assignments(activity_id);
CREATE INDEX IF NOT EXISTS ix_asg_resource ON assignments(resource_id);

CREATE TABLE IF NOT EXISTS baselines (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS baseline_activities (
  baseline_id INTEGER NOT NULL REFERENCES baselines(id) ON DELETE CASCADE,
  activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
  start TEXT,
  finish TEXT,
  work_hours REAL DEFAULT 0,
  cost REAL DEFAULT 0,
  duration_days INTEGER DEFAULT 0,
  PRIMARY KEY (baseline_id, activity_id)
);

CREATE TABLE IF NOT EXISTS progress (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
  date TEXT NOT NULL,
  percent_complete REAL NOT NULL,
  actual_hours REAL DEFAULT 0,
  actual_cost REAL DEFAULT 0,
  resource_id INTEGER REFERENCES resources(id) ON DELETE SET NULL,
  actual_start TEXT,
  actual_finish TEXT,
  note TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_prog_activity ON progress(activity_id, date);
CREATE INDEX IF NOT EXISTS ix_prog_resource ON progress(resource_id);

CREATE TABLE IF NOT EXISTS schedule_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  created_at TEXT DEFAULT (datetime('now')),
  kind TEXT NOT NULL,                  -- 'cpm' | 'optimize' | 'maintenance_level'
  weights TEXT,                        -- JSON ObjectiveWeights
  options TEXT,                        -- JSON OptimizeOptions
  metrics_before TEXT,                 -- JSON
  metrics_after TEXT,                  -- JSON
  result_path TEXT,                    -- archivo .npz/.json con starts/finishes/asignaciones (grande)
  applied INTEGER DEFAULT 0,
  status TEXT,
  elapsed_s REAL
);
