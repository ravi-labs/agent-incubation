import { NavLink, Route, Routes, Navigate } from "react-router-dom";
import Overview from "./pages/Overview";
import Agents from "./pages/Agents";

export default function App() {
  return (
    <div className="shell">
      <aside className="sidebar">
        <h1>Arc</h1>
        <div className="role">Dev · engineering</div>
        <nav className="nav">
          <NavLink to="/" end>Overview</NavLink>
          <NavLink to="/agents">Agents</NavLink>
        </nav>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/"       element={<Overview />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="*"       element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
