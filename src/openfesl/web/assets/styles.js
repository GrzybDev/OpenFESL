// Each component and page brings its own stylesheet. They follow the base stylesheet, whose
// cascade layers order them, as this module follows it in the entry's imports.
import.meta.glob(["../components/*.css", "../templates/*.css"], { eager: true });
