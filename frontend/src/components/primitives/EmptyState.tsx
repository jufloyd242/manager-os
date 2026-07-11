interface EmptyStateProps {
  message: string
  action?: string
  onAction?: () => void
}

export function EmptyState({ message, action, onAction }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center h-full p-8 text-center">
      <p className="text-sm text-slate-400">{message}</p>
      {action && onAction && (
        <button
          onClick={onAction}
          className="mt-3 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 cursor-pointer"
        >
          {action}
        </button>
      )}
    </div>
  )
}
