import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <section className="card">
      <h2>Page Not Found</h2>
      <p>The requested route does not exist in this phase.</p>
      <Link to="/">Go back to login</Link>
    </section>
  );
}
