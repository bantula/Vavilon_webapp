import { BrowserRouter as Router, Routes, Route } from 'react-router-dom'
import LandingPage from './pages/LandingPage'
import LoginPage from './pages/LoginPage'
import SpeakerPage from './pages/SpeakerPage'
import ListenerPage from './pages/ListenerPage'

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/speaker/:sessionId" element={<SpeakerPage />} />
        <Route path="/join" element={<ListenerPage />} />
      </Routes>
    </Router>
  )
}

export default App