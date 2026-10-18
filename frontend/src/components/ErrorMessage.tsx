import { ApiError } from "../api/client";

export function errorText(error: unknown): string {
  if (error instanceof ApiError && error.code === "validation_error") {
    const errors = (error.details.errors as { loc: string[]; msg: string }[] | undefined) ?? [];
    if (errors.length) return errors.map((e) => `${e.loc.at(-1)}: ${e.msg}`).join("; ");
  }
  return error instanceof Error ? error.message : "Something went wrong";
}

export default function ErrorMessage({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null;
  return (
    <div className="alert alert-error" role="alert">
      {errorText(error)}
      {onRetry && (
        <button className="link" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}
