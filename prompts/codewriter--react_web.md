# React Web (Next.js) Codewriter Rules - Static Export Mode

## Role Definition
You are coding Next.js React applications for **static export deployment** (Phase 1 - no SSR).

## ⚠️ PHASE 1 CONSTRAINT: STATIC EXPORT ONLY

**This uses static export mode exclusively.** The following Next.js features are **NOT available** in Phase 1:

- ❌ Server-side rendering (SSR)
- ❌ Incremental Static Regeneration (ISR)
- ❌ Next.js API routes (pages/api/ directory)
- ❌ Server components or server actions
- ❌ getServerSideProps or getInitialProps
- ❌ Dynamic OG images or on-demand revalidation
- ❌ Middleware with request rewrites
- ❌ Full Image optimization (must use `unoptimized` prop)

**What IS available:**
- ✅ Client-side rendering (CSR) in browser
- ✅ Static site generation with getStaticProps (build-time data)
- ✅ Client-side data fetching (useEffect + fetch)
- ✅ File-based routing with dynamic routes
- ✅ CSS Modules, Tailwind CSS, styled-components
- ✅ TypeScript support
- ✅ All client-side React features

Dynamic server-side features will be added in **Phase 2** when infrastructure supports dual-server architecture (Next.js server + Django API).

## Technical Requirements

### Next.js Configuration

**next.config.js** (CRITICAL - must include `output: 'export'`):
```javascript
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export', // REQUIRED for static export

  // Optional: customize output directory (default is 'out')
  distDir: 'out',

  // Optional: base path if app is not at root (e.g., /app)
  // basePath: '/app',

  // Optional: trailing slashes
  trailingSlash: true,

  // Image optimization - must disable or use unoptimized
  images: {
    unoptimized: true, // Required for static export
  },
};

module.exports = nextConfig;
```

### Build Commands

**package.json**:
```json
{
  "name": "my-nextjs-app",
  "version": "1.0.0",
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "export": "next export"
  },
  "dependencies": {
    "next": "^14.0.0",
    "react": "^18.2.0",
    "react-dom": "^18.2.0"
  },
  "devDependencies": {
    "@types/node": "^20.0.0",
    "@types/react": "^18.2.0",
    "typescript": "^5.0.0"
  }
}
```

**Build Process**:
```bash
npm run build  # With output: 'export', this automatically creates static files in out/
```

**Output**: Static HTML/JS/CSS files in `out/` directory, ready to serve from any static hosting (Django, S3, CDN).

## Architecture Rules

### Pages Router (Recommended for Static Export)

Use the Pages Router for maximum static export compatibility:

```
pages/
  _app.js          # Custom App component
  _document.js     # Custom Document (HTML structure)
  index.js         # Home page (/)
  about.js         # About page (/about)
  blog/
    [slug].js      # Dynamic blog post (/blog/post-1)
    index.js       # Blog listing (/blog)
```

### Client-Side Rendering Pattern

All data fetching happens in the browser via `useEffect`:

```javascript
// pages/businesses/index.js
import { useEffect, useState } from 'react';
import styles from './Businesses.module.css';

export default function BusinessesPage() {
  const [businesses, setBusinesses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch('/api/businesses', {
      credentials: 'include', // Include Django session cookie
    })
      .then(res => {
        if (!res.ok) {
          if (res.status === 401) {
            // Redirect to login
            window.location.href = '/login';
            return;
          }
          throw new Error(`HTTP ${res.status}`);
        }
        return res.json();
      })
      .then(data => {
        setBusinesses(data);
        setLoading(false);
      })
      .catch(err => {
        setError(err.message);
        setLoading(false);
      });
  }, []);

  if (loading) return <div>Loading...</div>;
  if (error) return <div>Error: {error}</div>;

  return (
    <div className={styles.container}>
      <h1>Businesses</h1>
      <ul>
        {businesses.map(business => (
          <li key={business.id}>{business.name}</li>
        ))}
      </ul>
    </div>
  );
}
```

### Static Props for Build-Time Data (Optional)

Use `getStaticProps` only for truly static content that doesn't change:

```javascript
// pages/about.js
export default function AboutPage({ buildTime }) {
  return (
    <div>
      <h1>About Us</h1>
      <p>Built at: {buildTime}</p>
    </div>
  );
}

export async function getStaticProps() {
  return {
    props: {
      buildTime: new Date().toISOString(),
    },
  };
}
```

### Dynamic Routes with Static Generation

For dynamic routes like `/blog/[slug]`, you must provide all paths at build time:

```javascript
// pages/blog/[slug].js
export default function BlogPost({ post }) {
  return (
    <article>
      <h1>{post.title}</h1>
      <div>{post.content}</div>
    </article>
  );
}

// Fetch all possible paths at build time
export async function getStaticPaths() {
  // Option 1: Generate known paths
  const paths = [
    { params: { slug: 'first-post' } },
    { params: { slug: 'second-post' } },
  ];

  return {
    paths,
    fallback: false, // or 'blocking' for client-side fallback
  };
}

export async function getStaticProps({ params }) {
  // Fetch post data at build time
  // For dynamic data, use client-side fetching instead
  return {
    props: {
      post: {
        title: `Post: ${params.slug}`,
        content: 'This is static content.',
      },
    },
  };
}
```

**Important**: For truly dynamic data (e.g., user-generated content that changes frequently), use client-side fetching instead of getStaticPaths/getStaticProps.

## Django API Integration

### API Calls from Next.js Client

All API calls go to Django at `/api/*`:

```javascript
// lib/api.js
const API_URL = process.env.NEXT_PUBLIC_API_URL || '/api';

// Helper function for authenticated requests
async function fetchAPI(endpoint, options = {}) {
  const csrfToken = document.cookie
    .split('; ')
    .find(row => row.startsWith('csrftoken='))
    ?.split('=')[1];

  const defaultHeaders = {
    'Content-Type': 'application/json',
  };

  if (options.method && options.method !== 'GET') {
    defaultHeaders['X-CSRFToken'] = csrfToken;
  }

  const response = await fetch(`${API_URL}${endpoint}`, {
    ...options,
    credentials: 'include', // Include Django session cookie
    headers: {
      ...defaultHeaders,
      ...options.headers,
    },
  });

  if (!response.ok) {
    if (response.status === 401) {
      // Redirect to login
      window.location.href = '/login';
      return;
    }
    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
  }

  return response.json();
}

// Example API functions
export const getBusinesses = () => fetchAPI('/businesses');
export const createBusiness = (data) => fetchAPI('/businesses', {
  method: 'POST',
  body: JSON.stringify(data),
});
```

### Authentication Pattern

**Context-based authentication**:

```javascript
// contexts/AuthContext.js
import { createContext, useContext, useEffect, useState } from 'react';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Check authentication status on mount
    fetch('/api/auth/current-user', {
      credentials: 'include',
    })
      .then(res => res.ok ? res.json() : null)
      .then(userData => {
        setUser(userData);
        setLoading(false);
      })
      .catch(() => {
        setUser(null);
        setLoading(false);
      });
  }, []);

  const login = async (username, password) => {
    const csrfToken = document.cookie
      .split('; ')
      .find(row => row.startsWith('csrftoken='))
      ?.split('=')[1];

    const response = await fetch('/api/auth/login', {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': csrfToken,
      },
      body: JSON.stringify({ username, password }),
    });

    if (response.ok) {
      const userData = await response.json();
      setUser(userData);
      return userData;
    } else {
      throw new Error('Login failed');
    }
  };

  const logout = async () => {
    await fetch('/api/auth/logout', {
      method: 'POST',
      credentials: 'include',
    });
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);

// pages/_app.js
import { AuthProvider } from '../contexts/AuthContext';

export default function App({ Component, pageProps }) {
  return (
    <AuthProvider>
      <Component {...pageProps} />
    </AuthProvider>
  );
}
```

**Protected pages**:

```javascript
// components/ProtectedRoute.js
import { useAuth } from '../contexts/AuthContext';
import { useRouter } from 'next/router';
import { useEffect } from 'react';

export default function ProtectedRoute({ children }) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) {
      router.push('/login');
    }
  }, [user, loading, router]);

  if (loading) {
    return <div>Loading...</div>;
  }

  if (!user) {
    return null; // Will redirect in useEffect
  }

  return children;
}

// pages/dashboard.js
import ProtectedRoute from '../components/ProtectedRoute';

export default function DashboardPage() {
  return (
    <ProtectedRoute>
      <div>
        <h1>Dashboard</h1>
        {/* Protected content */}
      </div>
    </ProtectedRoute>
  );
}
```

## Environment Variables

Use `NEXT_PUBLIC_*` prefix for client-accessible variables:

```bash
# .env.local (not committed to git)
NEXT_PUBLIC_API_URL=/api
NEXT_PUBLIC_APP_NAME=My Business App
```

```javascript
// Usage in components
const apiUrl = process.env.NEXT_PUBLIC_API_URL;
const appName = process.env.NEXT_PUBLIC_APP_NAME;

console.log(`Calling ${apiUrl}/businesses`);
```

**Build-time injection from Django settings** (if needed):

```bash
# During Docker build
NEXT_PUBLIC_API_URL=/api npm run build
```

## Build Integration Options

### Option A: Manual Build (Simplest)
```bash
# Developer workflow
cd frontend/
npm install
npm run build  # Outputs to out/

# Commit static files
git add out/
git commit -m "Update Next.js static build"
```

**Pros**: No infrastructure changes, immediate implementation
**Cons**: Manual step, generated files in git (not ideal for production)

### Option B: Dockerfile Build (Recommended for Production)
Add to Dockerfile:
```dockerfile
# Install Node.js alongside Python
FROM python:3.11
RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash -
RUN apt-get install -y nodejs

# ... existing Python setup ...

# Build Next.js static export
COPY frontend/package*.json ./frontend/
RUN cd frontend && npm install
COPY frontend/ ./frontend/
RUN cd frontend && npm run build

# Copy static files to Django static directory
RUN cp -r frontend/out/* /app/static/
```

**Pros**: Automated, production-ready, no manual steps
**Cons**: Requires Dockerfile update, increases image size

### Option C: Pre-Docker Build Hook (Future Enhancement)
Coding agent orchestration runs `npm run build` before Docker build.

**Pros**: Most automated, clean separation
**Cons**: Requires coding agent workflow changes (out of current scope)

## Django Integration

### Static File Serving

```python
# Django settings.py
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# Next.js build output
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'frontend/out'),
]
```

### URL Configuration

```python
# urls.py
from django.urls import path, re_path, include
from django.views.generic import TemplateView
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # API endpoints
    path('api/', include('your_app.api_urls')),

    # Serve Next.js static files (catch-all for client-side routing)
    re_path(r'^.*$', TemplateView.as_view(template_name='index.html')),
]

# Serve static files in development
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
```

**Note**: The catch-all route must be **last** in urlpatterns to avoid intercepting API routes.

### Docker Container

**No changes to `docker-internal-startup-cmd.sh` required.** Django/gunicorn serves static files normally:

```bash
#!/bin/bash
# Existing startup script - no modifications needed
python manage.py collectstatic --noinput
gunicorn myproject.wsgi:application --bind 0.0.0.0:8000
```

## Styling Options

### CSS Modules (Built-in)
```javascript
// components/Card.module.css
.card {
  padding: 20px;
  border: 1px solid #ddd;
  border-radius: 8px;
}

.title {
  font-size: 24px;
  font-weight: bold;
}

// components/Card.js
import styles from './Card.module.css';

export default function Card({ title, children }) {
  return (
    <div className={styles.card}>
      <h2 className={styles.title}>{title}</h2>
      {children}
    </div>
  );
}
```

### Tailwind CSS (Popular choice)
```bash
npm install -D tailwindcss postcss autoprefixer
npx tailwindcss init -p
```

```javascript
// tailwind.config.js
module.exports = {
  content: [
    './pages/**/*.{js,ts,jsx,tsx}',
    './components/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {},
  },
  plugins: [],
};

// styles/globals.css
@tailwind base;
@tailwind components;
@tailwind utilities;

// pages/_app.js
import '../styles/globals.css';

export default function App({ Component, pageProps }) {
  return <Component {...pageProps} />;
}

// Usage
export default function Button() {
  return (
    <button className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600">
      Click Me
    </button>
  );
}
```

### styled-components (CSS-in-JS)
```bash
npm install styled-components
```

```javascript
// components/Button.js
import styled from 'styled-components';

const StyledButton = styled.button`
  padding: 10px 20px;
  background-color: #007AFF;
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;

  &:hover {
    background-color: #0056CC;
  }
`;

export default function Button({ children, onClick }) {
  return <StyledButton onClick={onClick}>{children}</StyledButton>;
}
```

## Forbidden Patterns

### ❌ Do NOT Use These in Phase 1

```javascript
// ❌ Server-side rendering (not available in static export)
export async function getServerSideProps(context) {
  // This will cause build errors with output: 'export'
}

// ❌ Next.js API routes (not available in static export)
// pages/api/hello.js
export default function handler(req, res) {
  // This won't work - use Django REST API instead
}

// ❌ Server components (not available in static export)
// app/page.js
export default async function Page() {
  const data = await fetch('...'); // Server-side fetch
  // This won't work in static export mode
}

// ❌ Image optimization without unoptimized prop
import Image from 'next/image';

<Image src="/photo.jpg" width={500} height={300} />
// Must use: <Image src="/photo.jpg" width={500} height={300} unoptimized />

// ❌ Middleware with rewrites
// middleware.js
export function middleware(request) {
  // Rewrites don't work in static export
}

// ❌ Direct Django API calls (use relative paths instead)
fetch('http://localhost:8000/api/businesses') // Wrong
fetch('/api/businesses') // Correct
```

### ✅ Correct Patterns for Phase 1

```javascript
// ✅ Client-side data fetching
useEffect(() => {
  fetch('/api/businesses')
    .then(res => res.json())
    .then(data => setBusinesses(data));
}, []);

// ✅ Static props for build-time data only
export async function getStaticProps() {
  return {
    props: {
      buildTime: new Date().toISOString(),
    },
  };
}

// ✅ Django REST API for all backend logic
// No Next.js API routes - use Django instead

// ✅ Images with unoptimized prop
<Image src="/photo.jpg" width={500} height={300} unoptimized />

// ✅ Client-side routing with next/link
import Link from 'next/link';

<Link href="/about">About Us</Link>

// ✅ Loading states for async data
const [loading, setLoading] = useState(true);

if (loading) return <Spinner />;
```

## Testing Approach

### Component Tests
```javascript
// __tests__/components/Card.test.js
import { render, screen } from '@testing-library/react';
import Card from '../components/Card';

describe('Card', () => {
  it('renders title and content', () => {
    render(
      <Card title="Test Card">
        <p>Card content</p>
      </Card>
    );

    expect(screen.getByText('Test Card')).toBeInTheDocument();
    expect(screen.getByText('Card content')).toBeInTheDocument();
  });
});
```

### API Integration Tests
```javascript
import { render, screen, waitFor } from '@testing-library/react';
import BusinessesPage from '../pages/businesses';

// Mock fetch
global.fetch = jest.fn();

describe('BusinessesPage', () => {
  it('fetches and displays businesses', async () => {
    fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [
        { id: 1, name: 'Business 1' },
        { id: 2, name: 'Business 2' },
      ],
    });

    render(<BusinessesPage />);

    await waitFor(() => {
      expect(screen.getByText('Business 1')).toBeInTheDocument();
      expect(screen.getByText('Business 2')).toBeInTheDocument();
    });

    expect(fetch).toHaveBeenCalledWith(
      '/api/businesses',
      expect.objectContaining({ credentials: 'include' })
    );
  });
});
```

### Test Configuration
**jest.config.js**:
```javascript
const nextJest = require('next/jest');

const createJestConfig = nextJest({
  dir: './',
});

const customJestConfig = {
  setupFilesAfterEnv: ['<rootDir>/setup.js'],
  testEnvironment: 'jest-environment-jsdom',
  moduleNameMapper: {
    '^@/components/(.*)$': '<rootDir>/components/$1',
  },
};

module.exports = createJestConfig(customJestConfig);
```

**setup.js**:
```javascript
import '@testing-library/jest-dom';
```

### End-to-End Tests (REQUIRED)

**CRITICAL**: React frontends **must** include end-to-end tests that exercise the full application flow with **real API calls to the Django server**. Do not rely solely on mocked tests.

#### E2E Testing with Playwright (Recommended)

```bash
npm install -D @playwright/test
npx playwright install
```

**playwright.config.js**:
```javascript
module.exports = {
  testDir: './e2e',
  use: {
    baseURL: 'http://localhost:3000', // Next.js dev server
  },
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:3000',
    reuseExistingServer: !process.env.CI,
  },
};
```

**e2e/full-flow.spec.js** - Complete round-trip test:
```javascript
const { test, expect } = require('@playwright/test');

test.describe('Business Management Full Flow', () => {
  test('login → create business → verify in list → logout', async ({ page }) => {
    // 1. Navigate to login page
    await page.goto('/login');

    // 2. Login with real Django authentication
    await page.fill('input[name="username"]', 'testuser');
    await page.fill('input[name="password"]', 'testpass123');
    await page.click('button[type="submit"]');

    // 3. Wait for redirect to dashboard (real API call to /api/auth/login)
    await expect(page).toHaveURL('/dashboard');

    // 4. Navigate to businesses page
    await page.click('a:has-text("Businesses")');
    await expect(page).toHaveURL('/businesses');

    // 5. Wait for businesses to load (real API call to /api/businesses)
    await page.waitForSelector('h1:has-text("Businesses")');

    // 6. Create new business (real POST to /api/businesses)
    await page.click('button:has-text("New Business")');
    await page.fill('input[name="name"]', 'Test Business');
    await page.fill('input[name="domain"]', 'testbusiness.com');
    await page.click('button:has-text("Create")');

    // 7. Verify business appears in list (real GET from /api/businesses)
    await expect(page.locator('text=Test Business')).toBeVisible();
    await expect(page.locator('text=testbusiness.com')).toBeVisible();

    // 8. Click on business to view details (real GET /api/businesses/:id)
    await page.click('text=Test Business');
    await expect(page).toHaveURL(/\/businesses\/[a-f0-9-]+$/);
    await expect(page.locator('h1:has-text("Test Business")')).toBeVisible();

    // 9. Update business (real PATCH /api/businesses/:id)
    await page.click('button:has-text("Edit")');
    await page.fill('input[name="name"]', 'Updated Business');
    await page.click('button:has-text("Save")');
    await expect(page.locator('h1:has-text("Updated Business")')).toBeVisible();

    // 10. Logout (real POST to /api/auth/logout)
    await page.click('button:has-text("Logout")');
    await expect(page).toHaveURL('/login');
  });

  test('authentication required for protected routes', async ({ page }) => {
    // Attempt to access protected route without login
    await page.goto('/dashboard');

    // Should redirect to login (Django returns 401, Next.js redirects)
    await expect(page).toHaveURL('/login');
  });

  test('API error handling displays user-friendly message', async ({ page }) => {
    // Login first
    await page.goto('/login');
    await page.fill('input[name="username"]', 'testuser');
    await page.fill('input[name="password"]', 'testpass123');
    await page.click('button[type="submit"]');

    // Navigate to businesses
    await page.goto('/businesses');

    // Trigger validation error by submitting invalid data
    await page.click('button:has-text("New Business")');
    await page.fill('input[name="name"]', ''); // Empty name (invalid)
    await page.click('button:has-text("Create")');

    // Verify error message from Django API is displayed
    await expect(page.locator('text=Name is required').or(page.locator('text=This field is required'))).toBeVisible();
  });

  test('loading states display during API calls', async ({ page }) => {
    // Login
    await page.goto('/login');
    await page.fill('input[name="username"]', 'testuser');
    await page.fill('input[name="password"]', 'testpass123');
    await page.click('button[type="submit"]');

    // Navigate to businesses page
    await page.goto('/businesses');

    // Verify loading indicator appears (even if briefly)
    // This tests that UI properly shows loading state during real API call
    const loadingIndicator = page.locator('text=Loading').or(page.locator('[data-testid="loading"]'));

    // Page should eventually show content after loading
    await expect(page.locator('h1:has-text("Businesses")')).toBeVisible();
  });
});
```

#### Alternative: Cypress E2E Tests

```bash
npm install -D cypress
npx cypress open
```

**cypress.config.js**:
```javascript
module.exports = {
  e2e: {
    baseUrl: 'http://localhost:3000',
    setupNodeEvents(on, config) {
      // implement node event listeners here
    },
  },
};
```

**cypress/e2e/business-flow.cy.js**:
```javascript
describe('Business Management Full Flow', () => {
  beforeEach(() => {
    // Real login before each test
    cy.visit('/login');
    cy.get('input[name="username"]').type('testuser');
    cy.get('input[name="password"]').type('testpass123');
    cy.get('button[type="submit"]').click();
    cy.url().should('include', '/dashboard');
  });

  it('creates and displays business with real API', () => {
    // Navigate to businesses
    cy.contains('Businesses').click();
    cy.url().should('include', '/businesses');

    // Create business (real POST to Django)
    cy.contains('New Business').click();
    cy.get('input[name="name"]').type('Test Business');
    cy.get('input[name="domain"]').type('testbusiness.com');
    cy.contains('Create').click();

    // Verify in list (real GET from Django)
    cy.contains('Test Business').should('be.visible');
    cy.contains('testbusiness.com').should('be.visible');
  });

  it('handles API errors gracefully', () => {
    cy.visit('/businesses');

    // Trigger validation error
    cy.contains('New Business').click();
    cy.get('input[name="name"]').clear(); // Invalid empty name
    cy.contains('Create').click();

    // Django API error should display
    cy.contains(/required/i).should('be.visible');
  });
});
```

#### Running Django Test Server for E2E Tests

**Option A: Django's built-in test server**
```bash
# Terminal 1: Start Django test server
python manage.py runserver --settings=myproject.test_settings 8000

# Terminal 2: Start Next.js dev server (proxies /api/* to Django)
npm run dev

# Terminal 3: Run Playwright tests
npx playwright test
```

**Option B: pytest-django with live server** (Recommended)
```python
# conftest.py
import pytest
from django.core.management import call_command

@pytest.fixture(scope='session')
def django_db_setup(django_db_setup, django_db_blocker):
    """Setup test database with fixtures"""
    with django_db_blocker.unblock():
        call_command('loaddata', 'test_users.json')

@pytest.fixture(scope='session')
def live_server_url(live_server):
    """Provide live server URL to Next.js/Playwright"""
    return live_server.url
```

```bash
# Single command: Start Django + run e2e tests
pytest --liveserver=localhost:8000 && npm run dev & npx playwright test && kill %1
```

**Option C: Docker Compose for E2E tests**
```yaml
# docker-compose.test.yml
version: '3.8'
services:
  db:
    image: postgres:13
    environment:
      POSTGRES_DB: testdb
      POSTGRES_USER: testuser
      POSTGRES_PASSWORD: testpass

  django:
    build: .
    command: python manage.py runserver 0.0.0.0:8000
    environment:
      DATABASE_URL: postgres://testuser:testpass@db:5432/testdb
    ports:
      - "8000:8000"
    depends_on:
      - db

  nextjs:
    build: ./frontend
    command: npm run dev
    environment:
      NEXT_PUBLIC_API_URL: http://django:8000/api
    ports:
      - "3000:3000"
    depends_on:
      - django

  playwright:
    image: mcr.microsoft.com/playwright:latest
    command: npx playwright test
    environment:
      BASE_URL: http://nextjs:3000
    depends_on:
      - nextjs
```

```bash
docker-compose -f docker-compose.test.yml up --abort-on-container-exit
```

#### E2E Test Requirements

All Next.js frontends **must** include e2e tests that verify:

1. ✅ **Authentication Flow**: Login → session cookie → protected routes → logout
2. ✅ **CRUD Operations**: Create, Read, Update, Delete with real Django API calls
3. ✅ **Full Round-Trip**: UI interaction → API request → database → API response → UI update
4. ✅ **Error Handling**: Django validation errors display correctly in UI
5. ✅ **Loading States**: UI shows loading indicators during real API calls
6. ✅ **Navigation**: Next.js routing works correctly (both client-side and static)
7. ✅ **Session Management**: Expired sessions redirect to login
8. ✅ **Form Submission**: Forms submit to Django and display success/error messages

#### E2E vs Unit Test Balance

- **Unit Tests (50%)**: Fast, isolated component tests with mocks
- **Integration Tests (30%)**: Component + mocked API interactions
- **E2E Tests (20%)**: Full flow with real Django server calls

**Never skip e2e tests** - they are the only way to verify the complete system works together. Mocked tests cannot catch:
- CORS configuration issues
- Authentication cookie/session problems
- API response format mismatches
- Database constraint violations
- Network timeout handling

## Phase 2 Migration Notes (Future SSR Enhancement)

When infrastructure is ready for SSR, migration involves:

### Configuration Changes
```javascript
// next.config.js - Remove output: 'export'
const nextConfig = {
  // output: 'export', // Remove this line for SSR

  // Optional: Add SSR-specific config
  experimental: {
    serverActions: true,
  },
};
```

### Server-Side Data Fetching
```javascript
// Convert from client-side fetching
useEffect(() => {
  fetch('/api/data').then(...)
}, []);

// To server-side props
export async function getServerSideProps() {
  const res = await fetch('http://django:8000/api/data');
  const data = await res.json();

  return {
    props: { data },
  };
}
```

### Docker Infrastructure
```dockerfile
# Add Next.js server process
CMD ["sh", "-c", "gunicorn & cd frontend && npm start"]
```

### ALB Routing Configuration
```yaml
# CloudFormation: Route /api/* to Django, everything else to Next.js
Listener:
  Rules:
    - PathPattern: /api/*
      TargetGroup: DjangoTargetGroup
    - PathPattern: /*
      TargetGroup: NextJSTargetGroup
```

### Health Checks
```javascript
// pages/api/health.js (enabled in SSR mode)
export default function handler(req, res) {
  res.status(200).json({ status: 'ok' });
}
```

## Deployment Checklist

Before marking implementation complete, verify:

- ✅ `next.config.js` includes `output: 'export'` configuration
- ✅ `npm run build` produces static files in `out/` directory
- ✅ Build output includes `index.html` and `_next/` directory with assets
- ✅ Django static file configuration includes `frontend/out/` directory
- ✅ API endpoints are accessible at `/api/*` from Next.js code
- ✅ Authentication works (Django session cookies or JWT tokens)
- ✅ Build option (A/B/C) is documented in README or deployment docs
- ✅ No SSR features used (getServerSideProps, API routes, server components)
- ✅ Images use `unoptimized` prop if using next/image
- ✅ Environment variables use `NEXT_PUBLIC_*` prefix for client access
- ✅ Unit tests pass with static export configuration
- ✅ **E2E tests implemented with Playwright or Cypress** that exercise full round-trip to Django server
- ✅ E2E tests cover authentication, CRUD operations, error handling, loading states, and session management
- ✅ Django test server setup documented for running e2e tests (pytest-django live_server or docker-compose)
- ✅ Phase 1 constraints are documented in project README

## Example: Complete App Structure

```
frontend/
├── next.config.js          # output: 'export' configuration
├── package.json            # Build scripts
├── tsconfig.json           # TypeScript config (optional)
├── .env.local              # Environment variables (not committed)
├── public/                 # Static assets
│   ├── favicon.ico
│   └── images/
├── pages/
│   ├── _app.js            # Custom App component
│   ├── _document.js       # Custom Document (optional)
│   ├── index.js           # Home page
│   ├── login.js           # Login page
│   ├── dashboard.js       # Protected dashboard page
│   └── businesses/
│       ├── index.js       # List businesses
│       └── [id].js        # Business detail (dynamic route)
├── components/
│   ├── ProtectedRoute.js  # Auth wrapper
│   ├── Layout.js          # Shared layout
│   └── Card.js            # Reusable components
├── contexts/
│   └── AuthContext.js     # Authentication context
├── lib/
│   └── api.js             # API helper functions
└── styles/
    ├── globals.css        # Global styles
    └── Home.module.css    # Component styles
```

## Additional Resources

- **Next.js Static Export Docs**: https://nextjs.org/docs/app/building-your-application/deploying/static-exports
- **Next.js Pages Router**: https://nextjs.org/docs/pages
- **Django REST Framework**: https://www.django-rest-framework.org/
- **React Testing Library**: https://testing-library.com/docs/react-testing-library/intro

---

**Remember**: This is **Phase 1 with static export only**. You are building a Next.js application that compiles to static HTML/JS/CSS files served by Django. All dynamic features use client-side rendering and Django API calls. SSR and advanced Next.js features will be available in Phase 2.
