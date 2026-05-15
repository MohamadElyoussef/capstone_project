from app.db.session import SessionLocal
from app.db.models import Course, Section, ScheduledMeeting, Enrollment

def main():
    db = SessionLocal()
    try:
        course = db.query(Course).filter(Course.code == "IT101").first()
        if not course:
            print("IT101 not found, nothing to delete.")
            return

        sec_ids = [row[0] for row in db.query(Section.id).filter(Section.course_id == course.id).all()]

        db.query(ScheduledMeeting).filter(ScheduledMeeting.section_id.in_(sec_ids)).delete(synchronize_session=False)
        db.query(Enrollment).filter(Enrollment.section_id.in_(sec_ids)).delete(synchronize_session=False)
        db.query(Section).filter(Section.id.in_(sec_ids)).delete(synchronize_session=False)
        db.query(Course).filter(Course.id == course.id).delete(synchronize_session=False)

        db.commit()
        print(f"Deleted IT101, sections deleted: {len(sec_ids)}")
    finally:
        db.close()

if __name__ == "__main__":
    main()