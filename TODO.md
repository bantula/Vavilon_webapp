# TODO - Future Features

Organized by priority and phase.

## Phase 1: MVP Completion (Current)

- [x] Session creation and management
- [x] WebSocket broadcasting
- [x] Speaker microphone capture
- [x] Listener audio playback
- [x] Live subtitles
- [x] QR code generation
- [x] Multi-language support (10 languages)
- [x] Azure Speech SDK integration

## Phase 2: Production Readiness (Next 2-4 weeks)

### Critical
- [ ] Replace in-memory sessions with Redis
- [ ] Add proper error handling and retries
- [ ] Implement connection recovery (WebSocket reconnect)
- [ ] Add rate limiting (prevent abuse)
- [ ] Implement health monitoring
- [ ] Add structured logging
- [ ] Create load tests (200+ concurrent users)
- [ ] Deploy to Azure (staging environment)

### Important
- [ ] Add session expiration (auto-cleanup)
- [ ] Implement audio buffering (reduce dropouts)
- [ ] Add network quality indicators
- [ ] Create admin endpoint (session management)
- [ ] Add basic analytics (session duration, language usage)
- [ ] Improve error messages for users
- [ ] Add browser compatibility warnings

### Nice to Have
- [ ] Session recording (save translated audio)
- [ ] Download session transcripts
- [ ] Customize QR code branding
- [ ] Add session passwords (optional)
- [ ] Speaker notes/prompts feature

## Phase 3: Enhanced Features (2-3 months)

### Listener Experience
- [ ] Q&A mode (listeners can ask questions)
  - Toggle Q&A on/off
  - Listener raises hand
  - Speaker selects question
  - Question translated and read aloud
- [ ] Playback speed control (0.5x - 2x)
- [ ] Volume controls per listener
- [ ] Closed captions customization (size, color)
- [ ] Save favorite sessions
- [ ] Feedback/rating system

### Speaker Experience
- [ ] Multi-speaker support (panel discussions)
- [ ] Speaker switching (handoff)
- [ ] Speaker dashboard (advanced stats)
- [ ] Pre-recorded audio upload
- [ ] Schedule sessions in advance
- [ ] Speaker notes and slides sync

### Technical
- [ ] Mobile apps (iOS/Android)
- [ ] Offline mode (cache translations)
- [ ] WebRTC for lower latency
- [ ] Improve translation quality (context)
- [ ] Custom vocabulary/terminology
- [ ] Language auto-detection
- [ ] Continuous recognition (no button press)

## Phase 4: Enterprise Features (6+ months)

### Authentication & Users
- [ ] User accounts (email/password)
- [ ] SSO integration (Azure AD, Google)
- [ ] User profiles and preferences
- [ ] Session history
- [ ] Saved translations library

### Organization Management
- [ ] Organization accounts
- [ ] Team management (roles: admin, speaker, viewer)
- [ ] Billing and subscriptions
- [ ] Usage analytics dashboard
- [ ] White-label customization
- [ ] API access for integrations

### Advanced AI
- [ ] Speaker identification (diarization)
- [ ] Emotion/tone preservation in TTS
- [ ] Real-time accent adaptation
- [ ] Domain-specific models (medical, legal)
- [ ] Custom voice cloning (maintain speaker voice)

### Compliance & Security
- [ ] GDPR compliance
- [ ] Data encryption at rest
- [ ] Audio redaction (sensitive info)
- [ ] Audit logs
- [ ] SOC 2 certification prep

### Integrations
- [ ] Zoom/Teams plugin
- [ ] Calendar integration (Google, Outlook)
- [ ] Slack notifications
- [ ] Webhooks for events
- [ ] REST API for third-party apps
- [ ] Export to subtitle formats (SRT, VTT)

## Known Bugs / Issues

### High Priority
- [ ] Audio sync issues when network is slow
- [ ] WebSocket disconnects on mobile browsers
- [ ] QR code sometimes doesn't scan on iOS

### Medium Priority
- [ ] Long sessions (>1hr) cause memory issues
- [ ] Subtitle text sometimes overlaps
- [ ] Session code collision (rare)
- [ ] Browser microphone permission unclear

### Low Priority
- [ ] UI not optimized for tablets
- [ ] Dark mode not implemented
- [ ] No keyboard shortcuts
- [ ] Loading states inconsistent

## Technical Debt

- [ ] Add TypeScript to backend
- [ ] Write unit tests (aim for 80% coverage)
- [ ] Add integration tests
- [ ] Add E2E tests (Playwright)
- [ ] Document all APIs (OpenAPI/Swagger)
- [ ] Refactor WebSocket handler (too long)
- [ ] Extract audio processing to separate service
- [ ] Optimize bundle size (frontend)
- [ ] Add code linting (ESLint, Prettier)
- [ ] Setup pre-commit hooks

## Performance Optimizations

- [ ] Implement audio compression (reduce bandwidth)
- [ ] Add CDN for static assets
- [ ] Lazy load components
- [ ] Optimize Azure Speech SDK usage (batch requests)
- [ ] Implement Redis pub/sub for scaling
- [ ] Add database connection pooling
- [ ] Cache translated phrases (reduce API calls)
- [ ] Implement service worker (PWA)

## Documentation Needed

- [ ] API documentation (complete)
- [ ] Architecture diagrams (detailed)
- [ ] Sequence diagrams (data flow)
- [ ] Deployment runbook
- [ ] Disaster recovery plan
- [ ] User guide (speaker)
- [ ] User guide (listener)
- [ ] Admin guide
- [ ] Developer onboarding guide
- [ ] Troubleshooting guide

## Research / Spikes

- [ ] WebRTC vs WebSocket for audio (latency comparison)
- [ ] Alternative to Azure (cost comparison)
- [ ] Real-time translation quality (Azure vs Google vs AWS)
- [ ] Mobile app framework (React Native vs Flutter)
- [ ] Database options (Redis vs PostgreSQL for sessions)
- [ ] Horizontal scaling strategies
- [ ] AI model fine-tuning for domain accuracy

## Marketing / Business

- [ ] Create demo video
- [ ] Landing page optimization
- [ ] Pricing model research
- [ ] Competitor analysis
- [ ] User interviews (tour agencies)
- [ ] Case studies (early adopters)
- [ ] Blog posts (SEO)
- [ ] Social media presence

---

## How to Prioritize

1. **P0 (Critical)**: Blocks production launch
2. **P1 (High)**: Major user pain points
3. **P2 (Medium)**: Quality of life improvements
4. **P3 (Low)**: Nice to have features

## Decision Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2024-02-10 | Use Azure Speech SDK | Required by user, enterprise-grade |
| 2024-02-10 | No authentication in MVP | Speed to market, simplicity |
| 2024-02-10 | WebSocket over WebRTC | Simpler implementation, good enough for MVP |
| 2024-02-10 | React for frontend | Team familiarity, fast development |

---

**Last Updated**: 2024-02-10

For questions or to propose new features, contact the dev team.