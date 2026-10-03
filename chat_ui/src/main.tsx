import React from 'react'
import ReactDOM from 'react-dom/client'
import { ChatWindow } from './ChatWindow'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ChatWindow />
  </React.StrictMode>,
)
