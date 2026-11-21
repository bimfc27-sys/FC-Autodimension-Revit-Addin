"""Regenerates src/schema.ts from migrations/*.sql (embedded for automatic first-run setup)."""
import glob, json, os
os.chdir(os.path.join(os.path.dirname(__file__), ".."))
sql = [open(f).read() for f in sorted(glob.glob("migrations/*.sql"))]
open("src/schema.ts", "w").write("// Generated from migrations/*.sql by scripts/gen-schema.py - used for automatic first-run setup.\nexport const SCHEMA_SQL: string[] = " + json.dumps(sql) + ";\n")
