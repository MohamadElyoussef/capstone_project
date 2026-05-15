from sqlalchemy import Column, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.db.base_class import Base


class CourseEligibility(Base):
    __tablename__ = "course_eligibility"

    id = Column(Integer, primary_key=True, index=True)

    course_id = Column(Integer, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)

    # مثال: "Information Technology" أو "Data Analytics" أو "Information Systems"
    major = Column(String, nullable=False, index=True)

    # سنة الطالب المطلوبة (حد أدنى)
    min_year_level = Column(Integer, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint("course_id", "major", name="uq_course_eligibility_course_major"),
    )

    course = relationship("Course", back_populates="eligibilities")