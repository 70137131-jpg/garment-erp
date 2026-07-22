import { Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import Dashboard from "./pages/Dashboard";
import Masters from "./pages/Masters";
import Styles from "./pages/Styles";
import StyleDetail from "./pages/StyleDetail";
import Sales from "./pages/Sales";
import SalesDetail from "./pages/SalesDetail";
import Procurement from "./pages/Procurement";
import Inventory from "./pages/Inventory";
import Production from "./pages/Production";
import Quality from "./pages/Quality";
import Costing from "./pages/Costing";
import Finance from "./pages/Finance";

export default function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/masters" element={<Masters />} />
        <Route path="/styles" element={<Styles />} />
        <Route path="/styles/:id" element={<StyleDetail />} />
        <Route path="/sales" element={<Sales />} />
        <Route path="/sales/:id" element={<SalesDetail />} />
        <Route path="/procurement" element={<Procurement />} />
        <Route path="/inventory" element={<Inventory />} />
        <Route path="/production" element={<Production />} />
        <Route path="/quality" element={<Quality />} />
        <Route path="/costing" element={<Costing />} />
        <Route path="/finance" element={<Finance />} />
      </Routes>
    </Shell>
  );
}
