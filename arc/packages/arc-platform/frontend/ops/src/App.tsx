import { NavLink, Route, Routes, Navigate } from "react-router-dom";
import Overview from "./pages/Overview";
import Agents from "./pages/Agents";
import Approvals from "./pages/Approvals";

export default function App() {
  return (
    <div className="shell">
      <aside className="sidebar">
        <h1>Arc</h1>
        <div className="role">Ops · governance</div>
        <nav className="nav">
          <NavLink to="/" end>Overview</NavLink>
          <NavLink to="/agents">Agent inventory</NavLink>
          <NavLink to="/approvals">Approvals</NavLink>
        </nav>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/"          element={<Overview />} />
          <Route path="/agents"    element={<Agents />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="*"          element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
