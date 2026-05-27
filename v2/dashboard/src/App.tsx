import { Navigate, Route, Routes } from "react-router-dom";
import Sidebar from "./components/Sidebar";
import Live from "./pages/Live";
import Incidents from "./pages/Incidents";
import Sensors from "./pages/Sensors";
import Workers from "./pages/Workers";
import Zones from "./pages/Zones";
import Webhooks from "./pages/Webhooks";
import MLLab from "./pages/MLLab";
import Health from "./pages/Health";
import Settings from "./pages/Settings";

export default function App() {
  return (
    <div className="shell">
      <Sidebar />
      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to="/live" replace />} />
          <Route path="/live"      element={<Live />} />
          <Route path="/incidents" element={<Incidents />} />
          <Route path="/sensors"   element={<Sensors />} />
          <Route path="/workers"   element={<Workers />} />
          <Route path="/zones"     element={<Zones />} />
          <Route path="/webhooks"  element={<Webhooks />} />
          <Route path="/ml-lab"    element={<MLLab />} />
          <Route path="/health"    element={<Health />} />
          <Route path="/settings"  element={<Settings />} />
        </Routes>
      </main>
    </div>
  );
}
