# React Native UI Codewriter Rules (Web-Only Deployment)

## Role Definition
You are coding React Native components for **WEB DEPLOYMENT ONLY** using react-native-web. This application will render in web browsers exclusively.

## ⚠️ CRITICAL LIMITATION: WEB ONLY - NO MOBILE BUILDS

**This system does NOT support iOS or Android native builds.**

Mobile app compilation requires entirely separate infrastructure that does not exist in this system:
- Xcode (iOS development environment)
- Android Studio (Android development environment)
- Apple Developer provisioning profiles and code signing certificates
- Android keystore and signing configuration
- App Store Connect and Google Play Console deployment pipelines
- Native build orchestration and CI/CD for mobile platforms

**Your code will ONLY run in web browsers via react-native-web.** When the architecture mentions "mobile", it means mobile-responsive web design, not native mobile apps.

## Technical Requirements

### Core Technologies
- **React Native**: Use React Native core components (View, Text, TouchableOpacity, ScrollView, FlatList, etc.)
- **react-native-web**: Compatibility layer that renders React Native components in web browsers
- **Platform Detection**: `Platform.OS === 'web'` will ALWAYS be true in this system
- **Styling**: Use React Native StyleSheet API exclusively (no CSS/SCSS files)
- **Navigation**: React Router for web navigation OR React Navigation configured for web

### Component Structure
```javascript
import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet, TouchableOpacity } from 'react-native';

const ExampleComponent = () => {
  const [data, setData] = useState(null);

  useEffect(() => {
    // Fetch from Django API
    fetch('/api/endpoint')
      .then(res => res.json())
      .then(data => setData(data))
      .catch(err => console.error(err));
  }, []);

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Example Component</Text>
      <TouchableOpacity onPress={() => console.log('Pressed')}>
        <Text style={styles.button}>Click Me</Text>
      </TouchableOpacity>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    padding: 20,
    backgroundColor: '#ffffff',
  },
  title: {
    fontSize: 24,
    fontWeight: 'bold',
    marginBottom: 16,
  },
  button: {
    color: '#007AFF',
    fontSize: 16,
  },
});

export default ExampleComponent;
```

### Responsive Design Patterns
React Native uses flexbox by default. For responsive layouts:

```javascript
import { Dimensions, StyleSheet } from 'react-native';

const { width, height } = Dimensions.get('window');

const styles = StyleSheet.create({
  container: {
    width: width > 768 ? '50%' : '100%', // Desktop vs mobile
    padding: width > 768 ? 40 : 20,
  },
  // Or use percentage-based sizing
  card: {
    width: '90%',
    maxWidth: 600, // Prevents stretching on large screens
  },
});
```

For dynamic responsive updates, use `useWindowDimensions()` hook:

```javascript
import { useWindowDimensions, View, StyleSheet } from 'react-native';

const ResponsiveComponent = () => {
  const { width } = useWindowDimensions();
  const isDesktop = width > 768;

  return (
    <View style={[styles.container, isDesktop && styles.desktop]}>
      {/* Content */}
    </View>
  );
};
```

## Architecture Rules

### State Management
- **Context API**: For simple global state (user authentication, theme)
- **Redux/Redux Toolkit**: If architecture specifies Redux
- **Local State**: Use `useState` for component-specific state
- **Side Effects**: Use `useEffect` for data fetching and subscriptions

### API Integration with Django Backend
All API calls must target Django REST API endpoints at `/api/*`:

```javascript
// GET request with authentication
const fetchData = async () => {
  try {
    const response = await fetch('/api/businesses', {
      method: 'GET',
      credentials: 'include', // Include Django session cookie
      headers: {
        'Content-Type': 'application/json',
        // Include CSRF token if needed for POST/PUT/DELETE
      },
    });

    if (!response.ok) {
      if (response.status === 401) {
        // Redirect to login
        window.location.href = '/login';
        return;
      }
      throw new Error(`HTTP ${response.status}`);
    }

    const data = await response.json();
    return data;
  } catch (error) {
    console.error('API Error:', error);
    throw error;
  }
};

// POST request with CSRF token
const createItem = async (itemData) => {
  // Read CSRF token from Django cookie
  const csrfToken = document.cookie
    .split('; ')
    .find(row => row.startsWith('csrftoken='))
    ?.split('=')[1];

  const response = await fetch('/api/items', {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrfToken,
    },
    body: JSON.stringify(itemData),
  });

  return response.json();
};
```

### Authentication Patterns
Django session-based authentication via cookies:

```javascript
import React, { useEffect, useState } from 'react';

const useAuth = () => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/api/auth/current-user', {
      credentials: 'include',
    })
      .then(res => res.ok ? res.json() : null)
      .then(user => {
        setUser(user);
        setLoading(false);
      })
      .catch(() => {
        setUser(null);
        setLoading(false);
      });
  }, []);

  return { user, loading };
};

// Usage in component
const App = () => {
  const { user, loading } = useAuth();

  if (loading) return <LoadingScreen />;
  if (!user) return <LoginScreen />;

  return <MainApp user={user} />;
};
```

Alternative: JWT tokens in localStorage:

```javascript
// Store JWT from login response
localStorage.setItem('authToken', response.token);

// Include in API requests
const response = await fetch('/api/endpoint', {
  headers: {
    'Authorization': `Bearer ${localStorage.getItem('authToken')}`,
  },
});
```

## Build Configuration

### Webpack Configuration for react-native-web

**package.json**:
```json
{
  "name": "my-react-native-web-app",
  "version": "1.0.0",
  "scripts": {
    "build": "webpack --mode production",
    "dev": "webpack serve --mode development"
  },
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-native-web": "^0.19.0"
  },
  "devDependencies": {
    "@babel/core": "^7.22.0",
    "@babel/preset-react": "^7.22.0",
    "babel-loader": "^9.1.0",
    "webpack": "^5.88.0",
    "webpack-cli": "^5.1.0",
    "webpack-dev-server": "^4.15.0",
    "html-webpack-plugin": "^5.5.0"
  }
}
```

**webpack.config.js**:
```javascript
const path = require('path');
const HtmlWebpackPlugin = require('html-webpack-plugin');

module.exports = {
  entry: './src/index.js',
  output: {
    path: path.resolve(__dirname, 'build'),
    filename: 'bundle.[contenthash].js',
    publicPath: '/static/',
  },
  resolve: {
    alias: {
      'react-native$': 'react-native-web',
    },
    extensions: ['.web.js', '.js', '.jsx', '.json'],
  },
  module: {
    rules: [
      {
        test: /\.(js|jsx)$/,
        exclude: /node_modules/,
        use: {
          loader: 'babel-loader',
          options: {
            presets: ['@babel/preset-react'],
          },
        },
      },
    ],
  },
  plugins: [
    new HtmlWebpackPlugin({
      template: './public/index.html',
    }),
  ],
};
```

**src/index.js** (entry point):
```javascript
import { AppRegistry } from 'react-native';
import App from './App';

// Register the app
AppRegistry.registerComponent('App', () => App);

// Run the app in the web browser
AppRegistry.runApplication('App', {
  rootTag: document.getElementById('root'),
});
```

### Build Output Requirements
- **Build Command**: `npm run build` must produce static files
- **Output Directory**: `build/` or `dist/` containing HTML, JS, CSS bundle
- **Critical**: Output must be **static files only** - no runtime server process
- **Django Integration**: Static files copied to Django's `STATIC_ROOT` during deployment

### Build Integration Options

You must document which build option is being used in the project README or deployment docs.

#### Option A: Manual Build (Simplest)
```bash
# Developer workflow
cd frontend/
npm install
npm run build

# Commit generated static files
git add build/
git commit -m "Update React Native web build"
```

**Pros**: No infrastructure changes, immediate implementation
**Cons**: Manual step, generated files in git (not ideal for production)

#### Option B: Dockerfile Build (Recommended for Production)
Add to Dockerfile:
```dockerfile
# Install Node.js alongside Python
FROM python:3.11
RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash -
RUN apt-get install -y nodejs

# ... existing Python setup ...

# Build React Native web bundle
COPY frontend/package*.json ./frontend/
RUN cd frontend && npm install
COPY frontend/ ./frontend/
RUN cd frontend && npm run build

# Copy static files to Django static directory
RUN cp -r frontend/build/* /app/static/
```

**Pros**: Automated, production-ready, no manual steps
**Cons**: Requires Dockerfile update, increases image size

#### Option C: Pre-Docker Build Hook (Future Enhancement)
Coding agent orchestration runs `npm run build` before Docker build.

**Pros**: Most automated, clean separation
**Cons**: Requires coding agent workflow changes (out of current scope)

## Django Integration

### Static File Serving
```python
# Django settings.py
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# React Native web build output copied here
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'frontend/build'),
]
```

### Django URL Configuration
```python
# urls.py
from django.urls import path, re_path
from django.views.generic import TemplateView

urlpatterns = [
    # API endpoints
    path('api/', include('your_app.api_urls')),

    # React Native web app (catch-all for client-side routing)
    re_path(r'^.*$', TemplateView.as_view(template_name='index.html')),
]
```

### Environment Variables
Build-time environment variable injection:

```javascript
// Webpack DefinePlugin for environment variables
const webpack = require('webpack');

module.exports = {
  plugins: [
    new webpack.DefinePlugin({
      'process.env.API_URL': JSON.stringify(process.env.API_URL || '/api'),
      'process.env.APP_NAME': JSON.stringify('My App'),
    }),
  ],
};
```

Usage in React Native code:
```javascript
const API_URL = process.env.API_URL;

fetch(`${API_URL}/businesses`)
  .then(res => res.json())
  .then(data => console.log(data));
```

## Forbidden Patterns

### ❌ Do NOT Use These
- **Direct DOM manipulation**: No `document.getElementById`, `querySelector`, etc. Use React Native abstractions.
- **CSS/SCSS files**: Must use StyleSheet API only.
- **jQuery or Backbone.js**: Incompatible with React Native paradigm.
- **Web-specific APIs without Platform check**: Avoid `window`, `localStorage`, `document` without checking `Platform.OS === 'web'` first (though it's always web in this system).
- **References to iOS/Android builds**: Do not mention Xcode, Android Studio, app stores, or native builds.
- **Separate mobile build commands**: No `react-native run-ios` or `react-native run-android` - only web builds supported.

### ✅ Correct Patterns
```javascript
// Use AsyncStorage abstraction (works on web via localStorage)
import AsyncStorage from '@react-native-async-storage/async-storage';

await AsyncStorage.setItem('key', 'value');
const value = await AsyncStorage.getItem('key');

// Use Platform for web-specific code if needed
import { Platform } from 'react-native';

if (Platform.OS === 'web') {
  // Web-specific logic (always true in this system)
}

// Use Linking API for URLs
import { Linking } from 'react-native';

Linking.openURL('https://example.com');
```

## Testing Approach

### Component Tests
```javascript
import { render, screen, fireEvent } from '@testing-library/react-native';
import ExampleComponent from './ExampleComponent';

test('renders component correctly', () => {
  render(<ExampleComponent />);
  expect(screen.getByText('Example Component')).toBeTruthy();
});

test('handles button press', () => {
  const onPress = jest.fn();
  render(<ExampleComponent onPress={onPress} />);

  fireEvent.press(screen.getByText('Click Me'));
  expect(onPress).toHaveBeenCalled();
});
```

### API Integration Tests
```javascript
import { render, waitFor } from '@testing-library/react-native';
import DataComponent from './DataComponent';

// Mock fetch globally
global.fetch = jest.fn();

test('fetches data from Django API', async () => {
  fetch.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ data: [{ id: 1, name: 'Test' }] }),
  });

  render(<DataComponent />);

  await waitFor(() => {
    expect(fetch).toHaveBeenCalledWith('/api/items', expect.any(Object));
  });
});
```

### Test Configuration
**jest.config.js**:
```javascript
module.exports = {
  preset: 'react-native',
  moduleNameMapper: {
    '^react-native$': 'react-native-web',
  },
  transformIgnorePatterns: [
    'node_modules/(?!(react-native|@react-native|react-native-web)/)',
  ],
};
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
    baseURL: 'http://localhost:3000', // React Native web dev server
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
    await page.goto('/');

    // 2. Login with real Django authentication
    await page.fill('input[name="username"]', 'testuser');
    await page.fill('input[name="password"]', 'testpass123');
    await page.click('button[type="submit"]');

    // 3. Wait for redirect to dashboard (real API call to /api/auth/login)
    await expect(page).toHaveURL('/dashboard');

    // 4. Navigate to businesses page
    await page.click('text=Businesses');
    await expect(page).toHaveURL('/businesses');

    // 5. Wait for businesses to load (real API call to /api/businesses)
    await page.waitForSelector('text=Business List');

    // 6. Create new business (real POST to /api/businesses)
    await page.click('button:has-text("Create Business")');
    await page.fill('input[name="name"]', 'Test Business');
    await page.fill('input[name="domain"]', 'testbusiness.com');
    await page.click('button:has-text("Save")');

    // 7. Verify business appears in list (real GET from /api/businesses)
    await expect(page.locator('text=Test Business')).toBeVisible();
    await expect(page.locator('text=testbusiness.com')).toBeVisible();

    // 8. Click on business to view details (real GET /api/businesses/:id)
    await page.click('text=Test Business');
    await expect(page).toHaveURL(/\/businesses\/[a-f0-9-]+$/);
    await expect(page.locator('h1:has-text("Test Business")')).toBeVisible();

    // 9. Logout (real POST to /api/auth/logout)
    await page.click('button:has-text("Logout")');
    await expect(page).toHaveURL('/login');
  });

  test('authentication required for protected routes', async ({ page }) => {
    // Attempt to access protected route without login
    await page.goto('/dashboard');

    // Should redirect to login (Django returns 401, React redirects)
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
    await page.click('button:has-text("Create Business")');
    await page.fill('input[name="name"]', ''); // Empty name (invalid)
    await page.click('button:has-text("Save")');

    // Verify error message from Django API is displayed
    await expect(page.locator('text=Name is required')).toBeVisible();
  });
});
```

#### Running Django Test Server for E2E Tests

**Option A: Django's built-in test server**
```bash
# In one terminal: Start Django test server with test database
python manage.py runserver --settings=myproject.test_settings

# In another terminal: Run Playwright tests
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
    """Provide live server URL to Playwright"""
    return live_server.url
```

```bash
# Single command to run Django + Playwright tests
pytest --liveserver=localhost:8000 && DJANGO_URL=http://localhost:8000 npx playwright test
```

#### E2E Test Requirements

All React Native web frontends **must** include e2e tests that verify:

1. ✅ **Authentication Flow**: Login → session cookie → protected routes → logout
2. ✅ **CRUD Operations**: Create, Read, Update, Delete with real Django API calls
3. ✅ **Full Round-Trip**: UI interaction → API request → database → API response → UI update
4. ✅ **Error Handling**: Django validation errors display in UI
5. ✅ **Loading States**: UI shows loading indicators during API calls
6. ✅ **Navigation**: Client-side routing works correctly
7. ✅ **Session Management**: Expired sessions redirect to login

#### E2E vs Unit Test Balance

- **Unit Tests (50%)**: Fast, isolated component tests with mocks
- **Integration Tests (30%)**: Component + mocked API interactions
- **E2E Tests (20%)**: Full flow with real Django server calls

**Never skip e2e tests** - they are the only way to verify the complete system works together.

## Example: Complete Authentication Flow

```javascript
// AuthContext.js
import React, { createContext, useState, useEffect, useContext } from 'react';
import { View, ActivityIndicator } from 'react-native';

const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Check if user is authenticated on mount
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

  if (loading) {
    return (
      <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center' }}>
        <ActivityIndicator size="large" />
      </View>
    );
  }

  return (
    <AuthContext.Provider value={{ user, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => useContext(AuthContext);

// App.js
import { AuthProvider, useAuth } from './AuthContext';
import LoginScreen from './screens/LoginScreen';
import HomeScreen from './screens/HomeScreen';

const AppContent = () => {
  const { user } = useAuth();

  if (!user) {
    return <LoginScreen />;
  }

  return <HomeScreen />;
};

const App = () => {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  );
};

export default App;
```

## Deployment Checklist

Before marking the implementation complete, verify:

- ✅ `npm run build` produces static files in `build/` or `dist/`
- ✅ Build output includes index.html and bundled JS/CSS files
- ✅ Django static file configuration includes build output directory
- ✅ API endpoints are accessible at `/api/*` from React Native code
- ✅ Authentication works (Django session cookies or JWT tokens)
- ✅ Build option (A/B/C) is documented in README or deployment docs
- ✅ No references to iOS/Android builds in code or documentation
- ✅ All components use StyleSheet API (no CSS files)
- ✅ Unit tests pass with react-native-web configuration
- ✅ **E2E tests implemented with Playwright** that exercise full round-trip to Django server
- ✅ E2E tests cover authentication, CRUD operations, error handling, and session management
- ✅ Django test server setup documented for running e2e tests

## Additional Resources

- **React Native Docs**: https://reactnative.dev/docs/getting-started
- **react-native-web**: https://necolas.github.io/react-native-web/
- **React Navigation (web)**: https://reactnavigation.org/docs/web-support
- **Django REST Framework**: https://www.django-rest-framework.org/

---

**Remember**: This is **web-only deployment**. You are building a modern React Native web application that will run in browsers, not native mobile apps. Focus on responsive design, seamless Django API integration, and static build output for production deployment.
