import { NavLink } from "react-router-dom";

interface NavItem {
  to: string;
  label: string;
}

const NAV: NavItem[] = [
  { to: "/live",      label: "Live" },
  { to: "/incidents", label: "Incidents" },
  { to: "/sensors",   label: "Sensors" },
  { to: "/workers",   label: "Workers" },
  { to: "/zones",     label: "Zones" },
  { to: "/webhooks",  label: "Webhooks" },
  { to: "/ml-lab",    label: "ML Lab" },
  { to: "/health",    label: "Health" },
  { to: "/settings",  label: "Settings" },
];

export default function Sidebar() {
  return (
    <nav className="sidebar">
      <h1>ITIPS</h1>
      {NAV.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          className={({ isActive }) => (isActive ? "active" : undefined)}
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}
