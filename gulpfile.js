const gulp = require('gulp');
const sass = require('gulp-sass')(require('sass'));
const stripComments = require('gulp-strip-comments');
const concat = require('gulp-concat');
const uglify = require('gulp-uglify');
const terser = require('gulp-terser');
const cleanCSS = require('gulp-clean-css');
const rename = require('gulp-rename');
const sourcemaps = require('gulp-sourcemaps');

// Define paths
const paths = {
    styles: {
        src: './erieiron_ui/sass/**/*.scss',
        dest: './erieiron_ui/static/compiled/'
    },
    scripts: {
        src: [
            './node_modules/jquery/dist/jquery.js',
            './node_modules/jquery.cookie/jquery.cookie.js',
            './node_modules/jquery-ui/dist/jquery-ui.js',
            './node_modules/underscore/underscore.js',
            './node_modules/backbone/backbone.js',
            './node_modules/bootstrap/dist/js/bootstrap.bundle.js',
            './erieiron_common/js/**/*.js',
            './erieiron_ui/js/**/*.js'
        ],
        dest: './erieiron_ui/static/compiled/'
    }
};

function style() {
    const timestamp = new Date().toISOString().replace(/[\-T:\.Z]/g, '');
    console.log("running style");
    return gulp.src(paths.styles.src)
        .pipe(sourcemaps.init())
        .pipe(sass({
            includePaths: ['./node_modules']
        }).on('error', sass.logError))
        .pipe(cleanCSS())
        .pipe(rename({
            suffix: `-${timestamp}`
        }))
        .pipe(sourcemaps.write())
        .pipe(gulp.dest(paths.styles.dest));
}

function script_dev() {
    const timestamp = new Date().toISOString().replace(/[\-T:\.Z]/g, '');

    console.log("running script - dev");
    return gulp.src(paths.scripts.src)
        .pipe(sourcemaps.init())
        .pipe(concat('erieiron_app.min.js'))
        // .pipe(terser())
        // .pipe(stripComments({safe: true}))
        .pipe(rename({
            suffix: `-${timestamp}`
        }))
        .pipe(sourcemaps.write())
        .pipe(gulp.dest(paths.scripts.dest));
}

function script() {
    const timestamp = new Date().toISOString().replace(/[\-T:\.Z]/g, '');

    console.log("running script - prod");
    return gulp.src(paths.scripts.src)
        .pipe(concat('erieiron_app.min.js'))
        // .pipe(terser())
        // .pipe(stripComments({safe: true}))
        .pipe(rename({
            suffix: `-${timestamp}`
        }))
        .pipe(sourcemaps.write())
        .pipe(gulp.dest(paths.scripts.dest));
}

function exec() {
    script();
    return style();
}

function exec_dev() {
    script_dev();
    return style();
}

function watch() {
    exec_dev();
    gulp.watch(paths.styles.src, style);
    gulp.watch(paths.scripts.src, script_dev);
}

exports.script = script;
exports.style = style;
exports.watch = watch;
exports.exec = exec;
