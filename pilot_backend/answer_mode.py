"""Additive single-answer routing; historical run storage stays unchanged."""

def single(db,family_id):
    return db.execute("SELECT 1 FROM family_answer_modes WHERE family_id=? AND mode='single'",(family_id,)).fetchone() is not None

def source_tier(db,run):
    if run['assignment_id'] is None and run['tier']=='short' and single(db,run['family_id']):
        return 'answer'
    return run['tier']
